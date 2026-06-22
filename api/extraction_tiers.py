"""
Tiered listing-field extraction.

Built directly from live inspection of 9 real, unrelated agency sites in
one session (June 2026) — traversgray, Richardson & Wrench Newtown,
Highland Property, Crystal Realty, Wiseberry, Viridity Real Estate, JBRE
Property, Park Properties, BresicWhitney, Pilcher Residential. Finding:
at least 8 genuinely different markup shapes for the same 3 facts
(address, price, sold-or-not) — there is no single selector that works
universally. What DOES generalize is a fixed CHECKING ORDER: try the
most structured, most likely-correct source first, fall through if
absent.

Tiers, in order:
  1. JSON-LD (schema.org/Product or similar) — confirmed present on
     Wiseberry, fully structured (address as sub-fields, numeric price,
     even lat/lon). Image URLs referenced agentboxcdn.com.au, suggesting
     this may generalize to any Agentbox-fed site, not just Wiseberry —
     UNCONFIRMED beyond the one example, worth testing further.
  2. Open Graph / Twitter Card meta tags — confirmed present on Highland
     Property (og:street-address, og:locality, og:region, og:postal-code,
     plus a twitter:title that embeds status and sold date as text).
     Coarser than JSON-LD (address fields are still discrete, but
     status/price often arrive embedded in a title/description string
     needing further parsing) but still far more reliable than scanning
     arbitrary div text.
  3. Known shared-template classes — confirmed IDENTICAL markup
     (class="prop-title pull-left/pull-right margin0", combined
     "Sold For $X" / "For Sale $X" text) across two unrelated-looking
     agencies, Viridity Real Estate and JBRE Property — proof that
     distinct agencies sometimes run literally the same underlying
     platform/template, worth checking explicitly rather than only via
     the fully generic price-class scan.
  3b. Known class="price"/class="address" pattern — the original
      confirmed Belle Property finding (predates this tiered module),
      kept as its own explicit tier rather than dropped.
  3c. Generic $ scan — the original fully-generic last resort (any
      element whose text is mostly a $ amount, paired with a plain
      <h1> for address). Highest false-positive risk of any tier; kept
      as a safety net below every more specific check.
  4. LLM extraction — the true last resort, used ONLY when tiers 1-3c
     all fail to find a usable address. Sends the page's relevant HTML
     to an LLM and asks it to extract address/price/status directly.
     This is the one tier that can plausibly handle a genuinely novel
     markup pattern (e.g. traversgray's hidden <input> fields, which
     none of tiers 1-3c would catch) without a human inspecting the
     page first.

     COST IS REAL AND NOT YET MEASURED AT SCALE (see README) — this
     tier should only ever run when explicitly enabled with an API key,
     and every call's real usage should be tracked so an honest
     cost-per-listing figure can be measured from real test runs rather
     than estimated.
"""

import re
import json
import requests

REQUEST_TIMEOUT = 20


def _parse_price(price_text):
    if not price_text:
        return ""
    m = re.search(r"\$\s*([\d,]+)", price_text)
    if not m:
        return ""
    try:
        return str(int(m.group(1).replace(",", "")))
    except ValueError:
        return ""


def try_json_ld(html, log=None):
    """
    Tier 1. Looks for a schema.org Product/RealEstateListing block with
    an offers.price and an address sub-object. Confirmed working shape
    (Wiseberry, June 2026):
        {"@type":"Product","name":"...","offers":{"@type":"Offer",
         "price":"923000","priceCurrency":"AUD"},
         "address":{"@type":"PostalAddress","streetAddress":"...",
           "addressLocality":"...","addressRegion":"...",
           "postalCode":"..."}}
    Returns a dict of found fields, or None if no usable JSON-LD found.
    """
    for block_match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE,
    ):
        raw = block_match.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            # JSON-LD can nest a list of typed objects under @graph too
            candidates = item.get("@graph", [item]) if isinstance(item.get("@graph"), list) else [item]
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                offers = candidate.get("offers")
                address_obj = candidate.get("address")
                if not offers and not address_obj:
                    continue

                price = ""
                if isinstance(offers, dict):
                    price = str(offers.get("price", "") or "")
                elif isinstance(offers, list) and offers:
                    price = str(offers[0].get("price", "") or "")

                address = ""
                suburb = ""
                postcode = ""
                if isinstance(address_obj, dict):
                    street = address_obj.get("streetAddress", "")
                    suburb = address_obj.get("addressLocality", "")
                    region = address_obj.get("addressRegion", "")
                    postcode = address_obj.get("postalCode", "")
                    address = ", ".join(p for p in [street, suburb, region, postcode] if p)

                if address or price:
                    if log:
                        log("    [tier 1: JSON-LD] found structured data")
                    return {
                        "address": address,
                        "suburb": suburb,
                        "postcode": postcode,
                        "price": price,
                        "tier": "json_ld",
                    }
    return None


def try_meta_tags(html, log=None):
    """
    Tier 2. Looks for Open Graph / Twitter Card address fields, plus a
    title/description field that often embeds status + price + date as
    text. Confirmed working shape (Highland Property, June 2026):
        <meta property="og:street-address" content="9 Lyle Street">
        <meta property="og:locality" content="Ryde">
        <meta property="og:region" content="NSW">
        <meta property="og:postal-code" content="2112">
        <meta name="twitter:title" content="9 Lyle Street, Ryde NSW 2112
          – Sold 19/06/2026">
    Returns a dict of found fields, or None if no usable meta tags found.
    """
    street = _meta_content(html, "og:street-address")
    suburb = _meta_content(html, "og:locality")
    region = _meta_content(html, "og:region")
    postcode = _meta_content(html, "og:postal-code")

    if not (street or suburb):
        return None

    address = ", ".join(p for p in [street, suburb, region, postcode] if p)

    title = _meta_content(html, "twitter:title") or _meta_content(html, "og:title") or ""
    status = "Sold" if re.search(r"\bsold\b", title, re.IGNORECASE) else "Active"

    description = _meta_content(html, "twitter:description") or _meta_content(html, "og:description") or ""
    price = _parse_price(title) or _parse_price(description)

    if log:
        log("    [tier 2: meta tags] found og:/twitter: address fields")
    return {
        "address": address,
        "suburb": suburb,
        "postcode": postcode,
        "price": price,
        "status": status,
        "tier": "meta_tags",
    }


def _meta_content(html, name):
    m = re.search(
        rf'<meta[^>]+(?:property|name)=["\']' + re.escape(name) + r'["\'][^>]+content=["\']([^"\']*)["\']',
        html, re.IGNORECASE,
    )
    return m.group(1).strip() if m else None


def try_known_shared_template(html, log=None):
    """
    Tier 3. Checks for a specific shared template confirmed IDENTICAL
    across two unrelated agencies (Viridity Real Estate, JBRE Property —
    June 2026):
        <h2 class="prop-title pull-left margin0" ...>Sold For $X</h2>
        <h2 class="prop-title pull-right margin0">{address}</h2>
    Proof that distinct-looking agencies sometimes run the exact same
    underlying platform — worth a dedicated check rather than relying
    solely on the fully generic scan in tier 4 (the old GenericFallback
    div-scanning logic, kept as the final layer below this module).
    """
    status_match = re.search(
        r'<h2[^>]*class="[^"]*prop-title[^"]*pull-left[^"]*"[^>]*>([^<]+)</h2>',
        html,
    )
    if not status_match:
        return None

    status_price_text = status_match.group(1).strip()
    is_sold = status_price_text.lower().startswith("sold")
    price = _parse_price(status_price_text)

    addr_match = re.search(
        r'<h2[^>]*class="[^"]*prop-title[^"]*pull-right[^"]*"[^>]*>([^<]+)</h2>',
        html,
    )
    address = addr_match.group(1).strip() if addr_match else ""

    if not address:
        return None

    if log:
        log("    [tier 3: known shared template - prop-title] matched")
    return {
        "address": address,
        "suburb": "",
        "postcode": "",
        "price": price,
        "status": "Sold" if is_sold else "Active",
        "tier": "known_template_prop_title",
    }


def try_class_price_address(html, log=None):
    """
    Tier 3b. Checks for the confirmed Belle Property pattern:
        <h1 class="address">{address}</h1>
        <div class="price">{price text, e.g. "Offers from $795,000"
          or "$1,335,000"}</div>
    Same class names appear for both active and sold listings on Belle;
    status is NOT determinable from this tier (the caller falls back to
    the URL-path /sold/ check, same as the original Belle-only adapter
    did before this tiered system existed).
    """
    addr_match = re.search(r'<h1[^>]*class="[^"]*\baddress\b[^"]*"[^>]*>([^<]+)</h1>', html)
    address = addr_match.group(1).strip() if addr_match else ""
    if not address:
        return None

    price_match = re.search(r'<div[^>]*class="[^"]*\bprice\b[^"]*"[^>]*>([^<]+)</div>', html)
    price_text = price_match.group(1).strip() if price_match else ""
    price = _parse_price(price_text)

    if log:
        log("    [tier 3b: class=price/class=address - Belle Property pattern] matched")
    return {
        "address": address,
        "suburb": "",
        "postcode": "",
        "price": price,
        "tier": "class_price_address",
    }


def try_generic_dollar_scan(html, log=None):
    """
    Tier 3c. The original, fully generic last-resort: any element whose
    text is mostly a $ amount, paired with a plain <h1> for address. The
    least reliable tier — highest false-positive risk (could match an
    unrelated price in page copy, a related-listings carousel, etc.) —
    kept as a safety net below every more specific tier.
    """
    addr_match = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
    address = addr_match.group(1).strip() if addr_match else ""
    if not address:
        return None

    price_match = re.search(r'>([^<]{0,40}\$[\d,]{4,}[^<]{0,20})<', html)
    price_text = price_match.group(1).strip() if price_match else ""
    price = _parse_price(price_text)

    if log:
        log("    [tier 3c: generic $ scan] matched (lowest-confidence tier)")
    return {
        "address": address,
        "suburb": "",
        "postcode": "",
        "price": price,
        "tier": "generic_dollar_scan",
    }


def try_llm_extraction(html, listing_url, api_key, log=None, model="claude-haiku-4-5-20251001"):
    """
    Tier 4 — the true last resort. Only called when tiers 1-3 all fail.
    Sends a trimmed slice of the page to an LLM and asks for structured
    JSON back. COST IS REAL: roughly one API call per listing that
    reaches this tier. Track every call so real per-listing cost can be
    measured from actual test runs (see README) rather than estimated.

    Returns a dict of found fields, or None if the API call fails or
    the response isn't usable JSON.
    """
    if not api_key:
        if log:
            log("    [tier 4: LLM] no API key provided, skipping this tier")
        return None

    # Trim the HTML to reduce token cost — strip script/style tags and
    # cap length. This is a blunt heuristic, not a guarantee of catching
    # the right section; a more refined version could try to isolate the
    # main content area first.
    trimmed = re.sub(r"<script.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    trimmed = re.sub(r"<style.*?</style>", "", trimmed, flags=re.DOTALL | re.IGNORECASE)
    trimmed = trimmed[:15000]

    prompt = (
        "Extract real estate listing data from this HTML. Respond with ONLY "
        "a JSON object, no other text, in this exact shape:\n"
        '{"address": "...", "suburb": "...", "price": "<digits only, no $ or commas, '
        'or empty string if no price found>", "status": "Active" or "Sold"}\n\n'
        f"HTML:\n{trimmed}"
    )

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        if log:
            log(f"    [tier 4: LLM] request failed for {listing_url}: {e}")
        return None

    if resp.status_code != 200:
        if log:
            log(f"    [tier 4: LLM] API returned HTTP {resp.status_code} for {listing_url}")
        return None

    try:
        data = resp.json()
        text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
        text = text.strip()
        # Strip markdown code fences if the model added them despite instructions
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        parsed = json.loads(text)
    except (json.JSONDecodeError, KeyError, AttributeError) as e:
        if log:
            log(f"    [tier 4: LLM] could not parse model response for {listing_url}: {e}")
        return None

    if not parsed.get("address"):
        return None

    if log:
        log(f"    [tier 4: LLM] extracted data for {listing_url}")
    return {
        "address": parsed.get("address", ""),
        "suburb": parsed.get("suburb", ""),
        "postcode": "",
        "price": _parse_price(f"${parsed.get('price', '')}") if parsed.get("price") else "",
        "status": parsed.get("status", "Active"),
        "tier": "llm_extraction",
    }


def extract_listing_fields(html, listing_url, log=None, llm_api_key=None):
    """
    Runs all tiers in order, returns the first usable result plus which
    tier produced it. This is the main entry point GenericFallbackAdapter
    (or any other adapter) should call instead of writing its own
    one-off extraction logic.
    """
    for tier_fn in (
        try_json_ld,
        try_meta_tags,
        try_known_shared_template,
        try_class_price_address,
        try_generic_dollar_scan,
    ):
        result = tier_fn(html, log=log)
        if result and result.get("address"):
            return result

    if llm_api_key:
        result = try_llm_extraction(html, listing_url, llm_api_key, log=log)
        if result and result.get("address"):
            return result

    return None
