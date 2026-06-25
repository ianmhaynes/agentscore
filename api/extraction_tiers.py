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

    CONFIRMED REAL STRUCTURE (via raw curl against a live Viridity
    listing page, June 2026) — the address h2 is NOT always a flat
    "<h2 ...>text</h2>" the way the original fixture assumed. The real
    page has a <br> and a nested <span> wrapping the actual text:
        <h2 class="prop-title pull-right margin0">
        <br>  <span style="font-size:0.8em;">76/3  Reid Avenue, Westmead</span>
    The original regex required NO tags between the h2's opening tag and
    its content ([^<]+), which silently failed on every real page using
    this nested form — a genuine bug, not a content issue, found by
    comparing this module's fixtures against real curl output. Fixed to
    capture everything up to the closing </h2> (including nested tags)
    and strip tags out afterward, rather than assuming flat text content.
    """
    status_match = re.search(
        r'<h2[^>]*class="[^"]*prop-title[^"]*pull-left[^"]*"[^>]*>(.*?)</h2>',
        html, re.DOTALL,
    )
    if not status_match:
        return None

    status_price_text = re.sub(r"<[^>]+>", " ", status_match.group(1)).strip()
    status_price_text = re.sub(r"\s+", " ", status_price_text)
    is_sold = status_price_text.lower().startswith("sold")
    price = _parse_price(status_price_text)

    addr_match = re.search(
        r'<h2[^>]*class="[^"]*prop-title[^"]*pull-right[^"]*"[^>]*>(.*?)</h2>',
        html, re.DOTALL,
    )
    if not addr_match:
        return None
    address = re.sub(r"<[^>]+>", " ", addr_match.group(1)).strip()
    address = re.sub(r"\s+", " ", address)

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


def try_semibold_muted_pattern(html, log=None):
    """
    Tier 3e. Confirmed real pattern (Pilcher Residential, confirmed via
    live DevTools inspection — June 2026):
        <div class="semi-bold">Sold</div>
        <div class="muted">$4,225,000</div>
    Status and price are SEPARATE sibling elements (unlike every other
    tier so far, which combines them in one string or field) — status
    in a "semi-bold" class, price in a "muted" class, no explicit
    address element confirmed alongside them at the time of inspection.
    Address must come from elsewhere on the page (URL slug or a nearby
    heading); this tier only confirms status+price, and the caller
    should still try to find an address via other means.
    """
    status_match = re.search(r'<div[^>]*class="[^"]*\bsemi-bold\b[^"]*"[^>]*>([^<]+)</div>', html)
    price_match = re.search(r'<div[^>]*class="[^"]*\bmuted\b[^"]*"[^>]*>([^<]+)</div>', html)
    if not status_match or not price_match:
        return None

    status_text = status_match.group(1).strip().lower()
    status = "Sold" if "sold" in status_text else "Active"
    price = _parse_price(price_match.group(1))

    # No confirmed dedicated address element for this pattern — fall
    # back to the page's <h1> or <title>-style heading, same generic
    # approach tier 3c uses, since address discovery wasn't the part of
    # this site that needed a custom tier.
    addr_match = re.search(r"<h1[^>]*>([^<]+)</h1>", html)
    address = addr_match.group(1).strip() if addr_match else ""
    if not address:
        return None

    if log:
        log("    [tier 3e: semi-bold/muted pattern] matched")
    return {
        "address": address,
        "suburb": "",
        "postcode": "",
        "price": price,
        "status": status,
        "tier": "semibold_muted_pattern",
    }


def try_renet_hidden_input_pattern(html, log=None):
    """
    Tier 3f. Confirmed real pattern (Travers Gray Real Estate, platform:
    ReNet — confirmed via "Marketing by ... and ReNet Real Estate
    Software" footer credit). CORRECTED June 23, 2026 after a real bug:
    an earlier version of this tier assumed a heading-pair structure
    (<h2>{suburb}</h2><h3>{street}</h3>) based on a web_fetch markdown
    conversion that turned out to misrepresent the real page — direct
    curl against the live site confirmed NO such suburb heading exists
    anywhere in the raw HTML. The real, reliable source of truth is a
    set of hidden form input fields (confirmed via raw curl,
    June 23, 2026):
        <input type="hidden" name="extra_data[address]" value="603/144 Mallett Street, Camperdown" />
        <input type="hidden" name="extra_data[heading]" value="UNDER OFFER!" />
        <input type="hidden" name="extra_data[price]" value="Sold for $555,000" />
    This is also the SAME hidden-input pattern first found on
    traversgray months ago at the very start of this project (then
    called the "decode '<input type=hidden name=extra_data[price]>'"
    finding) — meaning earlier tier-building work re-derived a worse,
    less reliable version of something already discovered. The address
    field already comes as "{street}, {suburb}" combined — no need to
    parse separate suburb/street headings at all.
    """
    addr_match = re.search(
        r'extra_data\[address\][^>]*value="([^"]+)"', html
    )
    if not addr_match:
        return None
    address = addr_match.group(1).strip()
    if not address:
        return None

    suburb = ""
    parts = [p.strip() for p in address.split(",")]
    if len(parts) >= 2:
        suburb = parts[-1]

    price_match = re.search(
        r'extra_data\[price\][^>]*value="([^"]+)"', html
    )
    status = ""
    price = ""
    if price_match:
        price_text = price_match.group(1).strip()
        is_sold = "sold" in price_text.lower()
        status = "Sold" if is_sold else "Active"
        price = _parse_price(price_text)
    else:
        # Some active listings show "Contact Agent" in the price field
        # or omit it; fall back to the heading field for status only.
        heading_match = re.search(r'extra_data\[heading\][^>]*value="([^"]+)"', html)
        if heading_match and "sold" in heading_match.group(1).lower():
            status = "Sold"

    if log:
        log("    [tier 3f: ReNet hidden-input pattern - extra_data[address/price]] matched")
    return {
        "address": address,
        "suburb": suburb,
        "postcode": "",
        "price": price,
        "status": status,
        "tier": "renet_hidden_input_pattern",
    }


def try_wordpress_epl_structured_pattern(html, log=None):
    """
    Tier 3j. Confirmed real STRUCTURED variant of the WordPress "EPL"
    plugin (The Melita Bell Team, RE/MAX Success franchise — confirmed
    via real raw HTML from a direct curl, June 24, 2026, NOT a
    markdown-converted guess — the original tier 3h, built from
    Woolloongabba Real Estate's simpler plain-text-inside-h1 version of
    this same plugin, did not match this site at all):
        <h1 class="entry-title">
            <span class="item-street">10/357 Margaret Street,</span>
            <span class="entry-title-sub">
                <span class="item-suburb">NEWTOWN</span>
                <span class="item-state">QLD</span>
                <span class="item-pcode">4350</span>
            </span>
        </h1>
        ...
        <span class="page-price sold-status">Sold &#036;660,000</span>
    Every address component has its OWN explicit semantic class —
    genuinely easier and more reliable to extract than tier 3h's plain
    text, since no fragile space/comma-counting heuristics are needed
    at all. Confirmed real detail: the price uses an HTML entity
    (&#036;) for the dollar sign, not a literal "$" character — must
    be decoded (or matched directly) rather than assumed.
    """
    street_match = re.search(r'class="item-street"[^>]*>([^<]+)<', html)
    if not street_match:
        return None
    street = street_match.group(1).strip().rstrip(",").strip()

    suburb_match = re.search(r'class="item-suburb"[^>]*>([^<]+)<', html)
    state_match = re.search(r'class="item-state"[^>]*>([^<]+)<', html)
    pcode_match = re.search(r'class="item-pcode"[^>]*>([^<]+)<', html)

    suburb = suburb_match.group(1).strip() if suburb_match else ""
    state = state_match.group(1).strip() if state_match else ""
    pcode = pcode_match.group(1).strip() if pcode_match else ""

    if not suburb:
        # Without at least a suburb, this isn't confirmed to be the
        # real structured EPL variant — step aside rather than return
        # a bare street with no real address context.
        return None

    address_parts = [p for p in [street, suburb, state, pcode] if p]
    address = ", ".join([street, " ".join([p for p in [suburb, state, pcode] if p])]) if street else ""
    if not address:
        return None

    price_status_match = re.search(
        r'class="[^"]*page-price[^"]*"[^>]*>([^<]+)<', html
    )
    status = ""
    price = ""
    if price_status_match:
        raw_text = price_status_match.group(1)
        # Confirmed real detail: the dollar sign is an HTML entity
        # (&#036;), decoded here rather than assumed to be a literal $.
        decoded_text = raw_text.replace("&#036;", "$").replace("&#36;", "$")
        if decoded_text.strip().lower().startswith("sold"):
            status = "Sold"
        elif "sale" in decoded_text.strip().lower():
            status = "Active"
        price = _parse_price(decoded_text)

    if log:
        log("    [tier 3j: WordPress EPL structured variant - explicit address spans] matched")
    return {
        "address": address,
        "suburb": suburb,
        "postcode": pcode,
        "price": price,
        "status": status,
        "tier": "wordpress_epl_structured_pattern",
    }


def try_wordpress_epl_pattern(html, log=None):
    """
    Tier 3h. Confirmed real pattern (Woolloongabba Real Estate,
    WordPress "EPL" real estate plugin — confirmed via
    "?action=epl_search&post_type=property" query params on the
    site's own nav links — June 24, 2026):
        <h1>47 Shore Street   Russell Island QLD 4184</h1>
        ...bed/bath/car icons...
        $475,000
    Address combined in a plain <h1> (full street + suburb + state +
    postcode, no separate elements), price as plain text nearby — no
    "Sold For" combined text confirmed for an ACTIVE listing (this
    site's sold listings, per the homepage card text, show "Sold" as
    a separate label from the address, not combined into one string
    the way some other platforms do).

    Built GENERICALLY (any <h1> + nearby $ amount) rather than
    targeting a specific CSS class, since only markdown-converted page
    content was available to confirm this structure, not raw HTML —
    a previous session's mistake of guessing exact class names from
    converted content (rather than verified raw bytes) caused a real,
    multi-hour debugging issue. A generic match is safer here: it
    risks matching IRRELEVANT h1 elements on a page that has more than
    one, but that's a lower-cost failure mode (skips a listing) than
    confidently extracting WRONG data from a guessed class name that
    doesn't actually exist in the real HTML.
    """
    addr_match = re.search(r"<h1[^>]*>([^<]+)</h1>", html)
    if not addr_match:
        return None
    raw_address_text = addr_match.group(1)
    address = re.sub(r"\s+", " ", raw_address_text).strip()
    if not address or len(address) < 8:
        return None

    # CONFIRMED REAL BUG (June 24, 2026): this tier originally matched
    # any page with an h1, even with no real price found after it,
    # which caused it to collide with a THIRD, later-added h1-based
    # tier (Rex Websites pattern, Kangaroo Point Real Estate) — that
    # platform puts its price BEFORE the h1, not after, so a genuine
    # Rex Websites page has no real $ amount in the text immediately
    # following its h1, yet this tier was still claiming the match
    # with an empty price. Fixed by requiring this tier's own real
    # distinguishing signature (a price genuinely found AFTER the
    # address) before claiming victory — the same fix pattern already
    # applied to the Eagle Software tier earlier today.
    after_address = html[addr_match.end():addr_match.end() + 1500]
    price_match = re.search(r"\$\s*[\d,]+", after_address)
    if not price_match:
        return None
    price = _parse_price(price_match.group(0))

    # Status: this platform shows "Sold" as a separate label on the
    # homepage card, not combined with the address/price text on the
    # detail page itself (confirmed: an active listing's detail page
    # has no "Sold" text near the price at all). Check for an explicit
    # "Sold" marker near the price; default to Active if absent, same
    # conservative approach as the Eagle Software tier.
    status = ""
    if re.search(r"(?:^|[>\s])Sold(?:[<\s]|$)", after_address, re.IGNORECASE):
        status = "Sold"

    suburb = ""
    # Confirmed real format 1: "{street}   {suburb} {STATE} {postcode}"
    # (multiple spaces between street and suburb in the real rendered
    # text, not a comma) — must check the RAW text (before whitespace
    # collapsing above), since the double-space is the only signal
    # distinguishing street from suburb.
    suburb_match = re.search(r"\s{2,}([A-Za-z\s]+?)\s+[A-Z]{2,3}\s+\d{4}\s*$", raw_address_text.strip())
    if suburb_match:
        suburb = suburb_match.group(1).strip()
    else:
        # Confirmed real format 2 (The Melita Bell Team, RE/MAX
        # Success — June 24, 2026): "{street}, {SUBURB} {STATE}
        # {postcode}" — a comma before the suburb instead of a
        # double-space. Same underlying WordPress real estate plugin
        # family producing a second real address format.
        comma_suburb_match = re.search(r",\s*([A-Za-z\s]+?)\s+[A-Z]{2,3}\s+\d{4}\s*$", address)
        if comma_suburb_match:
            suburb = comma_suburb_match.group(1).strip()

    if log:
        log("    [tier 3h: WordPress EPL pattern - h1 address + nearby price] matched")
    return {
        "address": address,
        "suburb": suburb,
        "postcode": "",
        "price": price,
        "status": status,
        "tier": "wordpress_epl_pattern",
    }


def try_rex_websites_pattern(html, log=None):
    """
    Tier 3i. Confirmed real pattern (Kangaroo Point Real Estate,
    platform: Rex Websites — confirmed via "Powered by Rex Websites"
    footer credit, June 24, 2026):
        SOLD
        $1,010,000
        # 8 / 50 Rotherham Street, Kangaroo Point QLD 4169
    Address combined in a plain <h1> (full street + suburb + state +
    postcode), with "SOLD" and price appearing as separate text BEFORE
    the h1 (not after, unlike most other tiers in this module) —
    confirmed real index-page cards also show this same SOLD-then-
    price-then-address-heading ordering. Distinct from BresicWhitney's
    "Rex CRM" (a confirmed JS-gated dead end from an earlier session) —
    "Rex Websites" is a different, fully server-rendered product from
    the same company.
    """
    addr_match = re.search(r"<h1[^>]*>([^<]+)</h1>", html)
    if not addr_match:
        return None
    address = re.sub(r"\s+", " ", addr_match.group(1)).strip()
    if not address or len(address) < 8:
        return None

    # CONFIRMED REAL BUG (June 24, 2026): this tier originally claimed
    # a match on ANY <h1>, even with no real price found BEFORE it —
    # which caused it to collide with the WordPress EPL tier (3h),
    # since that platform's price comes AFTER the h1, leaving nothing
    # in the "before" window for THIS tier to find, yet it was still
    # claiming the match with an empty price and a default "Active"
    # status. Fixed by requiring a genuine price to actually be found
    # before the h1 — this tier's own real distinguishing signature,
    # confirmed always present in the real Kangaroo Point Real Estate
    # structure this tier was built from. The same proven fix pattern
    # applied to Eagle Software and WordPress EPL earlier today, now
    # needed a fourth time.
    before_address = html[max(0, addr_match.start() - 500):addr_match.start()]
    price_match = re.search(r"\$\s*[\d,]+(?!\s*per)", before_address)
    if not price_match:
        return None
    price = _parse_price(price_match.group(0))

    status = "Sold" if re.search(r"(?:^|[>\s])SOLD(?:[<\s]|$)", before_address, re.IGNORECASE) else "Active"

    suburb = ""
    suburb_match = re.search(r",\s*([A-Za-z\s]+?)\s+[A-Z]{2,3}\s+\d{4}\s*$", address)
    if suburb_match:
        suburb = suburb_match.group(1).strip()

    if log:
        log("    [tier 3i: Rex Websites pattern - SOLD/price before h1 address] matched")
    return {
        "address": address,
        "suburb": suburb,
        "postcode": "",
        "price": price,
        "status": status,
        "tier": "rex_websites_pattern",
    }


def try_elders_franchise_pattern(html, log=None):
    """
    Tier 3k. Confirmed real pattern (Elders Smith and Elliott
    Townsville franchise office — June 25, 2026):
        <h1>15/3 Stanton Terrace</h1>
        <h2>Townsville City QLD 4810</h2>
        ...bed/bath/car...
        Offers over $699,000
    Street address ALONE in the h1 (no suburb at all), with suburb +
    state + postcode in the FOLLOWING h2 — distinct from the Eagle
    Software tier (3g), which expects the h2 to contain the PRICE, not
    the suburb. Confirmed real collision: Eagle Software's tier
    originally matched this page first (any h1 + any h2 is enough for
    it to claim a match), extracting only the street with no suburb/
    postcode/price at all. Checked specifically for the real
    "{Suburb} {STATE} {postcode}" shape in the h2 to distinguish this
    pattern from Eagle Software's price-in-h2 shape.
    """
    addr_match = re.search(r"<h1[^>]*>([^<]+)</h1>", html)
    if not addr_match:
        return None
    street = addr_match.group(1).strip()
    if not street:
        return None

    after_h1 = html[addr_match.end():addr_match.end() + 300]
    suburb_match = re.search(r"<h2[^>]*>([A-Za-z\s]+?)\s+([A-Z]{2,3})\s+(\d{4})</h2>", after_h1)
    if not suburb_match:
        # Without this specific suburb-state-postcode shape in the h2,
        # this isn't confirmed to be the real Elders pattern — step
        # aside rather than risk colliding with a different h1+h2 tier.
        return None
    suburb = suburb_match.group(1).strip()
    state = suburb_match.group(2)
    postcode = suburb_match.group(3)

    address = f"{street}, {suburb} {state} {postcode}"

    after_suburb = html[addr_match.end() + suburb_match.end():addr_match.end() + suburb_match.end() + 500]
    price_match = re.search(r"\$\s*[\d,]+", after_suburb)
    price = _parse_price(price_match.group(0)) if price_match else ""

    status = ""
    if re.search(r"\bsold\b", after_suburb, re.IGNORECASE):
        status = "Sold"

    if log:
        log("    [tier 3k: Elders franchise pattern - h1 street + h2 suburb/state/postcode] matched")
    return {
        "address": address,
        "suburb": suburb,
        "postcode": postcode,
        "price": price,
        "status": status,
        "tier": "elders_franchise_pattern",
    }


def try_eagle_software_pattern(html, log=None):
    """
    Tier 3g. Confirmed real pattern (Living Estate Agents, platform:
    Eagle Software — confirmed via "Powered by Eagle Software" footer
    credit, June 23, 2026):
        <h1>2 Chisholm Avenue, Clemton Park</h1>
        <h2>$1,827,000</h2>
    Address combined as one h1 ("street, suburb"), price alone in the
    next h2 — no "Sold"/"For Sale" text on the DETAIL page itself
    (confirmed: that status text only appears on the INDEX page's
    listing card, e.g. "Sold! $1,827,000", not on the individual
    listing page). Status must come from elsewhere — the caller falls
    back to the URL-path /sold-style check, same as Belle Property,
    since this tier intentionally does not guess a status itself.
    """
    addr_match = re.search(r"<h1[^>]*>([^<]+)</h1>", html)
    if not addr_match:
        return None
    address = addr_match.group(1).strip()
    if not address:
        return None

    # CONFIRMED REAL BUG (June 24, 2026): this tier originally matched
    # ANY page with an <h1>, even with no <h2> price found at all,
    # silently returning an empty price. That made it collide with a
    # different, later-added tier (WordPress EPL pattern,
    # Woolloongabba Real Estate) which ALSO uses a plain <h1> for the
    # address but puts the price as nearby plain text, not inside an
    # <h2>. Since this tier ran first in the pipeline, it was
    # incorrectly "winning" on EPL pages too, returning a wrong/empty
    # result instead of letting the correct tier match. Fixed by
    # requiring the actual confirmed <h2> price element to be found —
    # this tier's real, distinguishing signature — rather than treating
    # "has an h1" alone as sufficient to claim the match.
    price_match = re.search(r"<h2[^>]*>([^<]+)</h2>", html)
    if not price_match:
        return None
    price = _parse_price(price_match.group(1))

    suburb = ""
    parts = [p.strip() for p in address.split(",")]
    if len(parts) >= 2:
        suburb = parts[-1]

    if log:
        log("    [tier 3g: Eagle Software pattern - h1 address, h2 price] matched")
    return {
        "address": address,
        "suburb": suburb,
        "postcode": "",
        "price": price,
        "status": "",  # intentionally not guessed; caller falls back to URL-path check
        "tier": "eagle_software_pattern",
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


def try_reapit_agentbox_pattern(html, log=None):
    """
    Tier 3d. Confirmed real pattern shared by TWO distinct platforms —
    same <h4>-address + nearby-$-price shape, different status signal:

    Reapit/Agentbox (Crystal Realty, confirmed via "Powered by Reapit
    Websites" footer — June 2026):
        #### 13/54 Regent Street Chippendale NSW
        $ 890,000
        ...
        Contract
        Sold
    Explicit "Contract" label/value pair gives real status.

    Agentpoint (Park Properties, confirmed via "Powered by Agentpoint"
    footer — June 2026):
        #### 20/12-14 Enmore Road, NEWTOWN
        Sold
        $ 490,000
    NO "Contract" label at all — a standalone "Sold" text node appears
    on its own line right before the price instead. Checked as a
    fallback only when the Contract-field check finds nothing.

    Address in an <h4>, price as plain text nearby (not inside any
    specific class) — confirmed common to both. This is a genuinely
    different, fourth confirmed pattern family — not a variant of
    tier 3b (Belle's class="price"/class="address"), since neither the
    address nor price element here carries those specific class names.
    """
    addr_match = re.search(r"<h4[^>]*>([^<]+)</h4>", html)
    if not addr_match:
        return None
    address = addr_match.group(1).strip()
    if not address or len(address) < 5:
        return None

    # NOTE (June 24, 2026): a real false positive was found where an
    # unrelated <h4> site title on a Rex Websites page (Kangaroo Point
    # Real Estate) matched this tier purely because SOME price existed
    # within 500 chars of it. Two different attempts to fix THIS tier
    # directly (requiring a price unconditionally; requiring a price
    # AND a status signal) were both reverted after confirming each
    # one broke a real, already-tested, legitimate case this tier
    # needs to keep handling — a genuine active listing with no
    # parseable price at all ("Contact agent"), and a genuine active
    # listing with no status text shown at all. The false positive and
    # these legitimate cases are NOT reliably distinguishable using
    # only this tier's own signals. The real fix lives in pipeline
    # ORDERING instead (see extract_listing_fields) — try_rex_
    # websites_pattern now runs BEFORE this tier, since its own
    # signature is specific enough to correctly claim Rex Websites
    # pages first. This tier's own matching logic is intentionally
    # left exactly as originally built.
    after_address = html[addr_match.end():addr_match.end() + 500]
    price_match = re.search(r"\$\s*([\d,]+)", after_address)
    price = ""
    if price_match:
        try:
            price = str(int(price_match.group(1).replace(",", "")))
        except ValueError:
            price = ""

    # Status: confirmed explicit "Contract" label/value pair (Reapit/
    # Agentbox, Crystal Realty). A second, real platform — Agentpoint
    # (confirmed via Park Properties' own "Powered by Agentpoint"
    # footer credit) — shares this same <h4>-address + nearby-price
    # shape but has NO "Contract" label at all; instead a standalone
    # "Sold" text token appears as its own short line right before the
    # price. Checked as a fallback only if the Contract-field check
    # above found nothing, since the explicit label is more reliable
    # when present.
    status = ""
    # Confirmed real HTML structure via direct curl against the live
    # site (June 2026): "Contract" sits inside a <label>, and the value
    # sits in a SEPARATE sibling <div class="detail-value">, with a
    # newline between the closing </label> and the opening <div> tag:
    #   <label class="detail-label">Contract</label>
    #   <div class="detail-value">Sold</div>
    # A real bug was found and fixed here: the original regex's
    # tag-skipping group, (?:<[^>]*>)*, only matched tags with NO
    # whitespace between them, so it stopped at </label> and never
    # reached the actual value in the next div — every "Contract" field
    # silently failed to match on the real site despite working in every
    # hand-written test fixture (which never included a newline between
    # the tags). Fixed by allowing optional whitespace between each tag.
    contract_match = re.search(r"Contract(?:\s*<[^>]*>\s*)*([A-Za-z][A-Za-z ]*)", html)
    if contract_match:
        contract_value = contract_match.group(1).strip().lower()
        if "sold" in contract_value:
            status = "Sold"
        elif "sale" in contract_value:
            status = "Active"
    else:
        # Agentpoint fallback: a standalone "Sold" text node appearing
        # shortly before the price (confirmed: Park Properties shows
        # "Sold" on its own line directly above "$ 490,000"). Must
        # convert price_match's position (relative to the after_address
        # substring) back to an absolute offset in the full html before
        # slicing — a real bug found via testing: the original code
        # sliced html using an after_address-relative offset directly,
        # which pointed at the wrong region of the page entirely.
        # Guarded for price_match being None (e.g. a real "Contact
        # agent" listing with no parseable price at all) — without a
        # price match there's no reference position to anchor this
        # check against, so it's simply skipped, leaving status as "".
        if price_match:
            absolute_price_pos = addr_match.end() + price_match.start()
            before_price = html[max(0, absolute_price_pos - 200):absolute_price_pos]
            if re.search(r">\s*Sold\s*<", before_price, re.IGNORECASE):
                status = "Sold"
    # NOTE (June 24, 2026): an earlier version of this fix also
    # REQUIRED a status signal (Contract field or standalone Sold
    # text) to be found, attempting to resolve a real false positive
    # (an unrelated <h4> on a different platform's page, Kangaroo
    # Point Real Estate, matching purely because SOME price existed
    # within 500 chars of it). That additional requirement was
    # reverted because it ALSO broke a real, already-tested, valid
    # case: a genuine ACTIVE listing with no status text shown at all
    # (confirmed real, deliberately returns status="" rather than
    # guessing — see test_tier3d_agentpoint_standalone_sold_status).
    # The false positive and this genuine case are NOT distinguishable
    # using only tier 3d's own signals in isolation — both have a
    # price, neither has a Contract field or Sold text. The real fix
    # for the false positive belongs in tier ORDERING/routing (ensure
    # a more specific tier for that platform runs first), not in
    # making this tier reject valid data it cannot actually tell apart
    # from the bad case.

    if log:
        log("    [tier 3d: Reapit/Agentbox pattern - h4 address + Contract field] matched")
    return {
        "address": address,
        "suburb": "",
        "postcode": "",
        "price": price,
        "status": status,
        "tier": "reapit_agentbox_pattern",
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
        # try_rex_websites_pattern runs BEFORE try_reapit_agentbox_pattern
        # deliberately (June 24, 2026): a real false positive was found
        # where an unrelated <h4> site title on a Rex Websites page
        # (Kangaroo Point Real Estate) matched tier 3d purely because
        # SOME price existed within 500 chars of it. tier 3i's own
        # signature (status/price appearing BEFORE an <h1>, not near
        # any <h4> at all) is specific enough that it correctly claims
        # Rex Websites pages first, before tier 3d ever gets a chance
        # to wrongly match the unrelated heading. Tier 3d itself was
        # deliberately NOT made stricter to reject this case directly,
        # since doing so also broke a real, valid case it cannot
        # actually distinguish from the false positive using its own
        # signals alone (a genuine active listing with no status text
        # at all) — see try_reapit_agentbox_pattern's own comments.
        try_rex_websites_pattern,
        try_elders_franchise_pattern,
        try_reapit_agentbox_pattern,
        try_semibold_muted_pattern,
        try_renet_hidden_input_pattern,
        try_eagle_software_pattern,
        try_wordpress_epl_structured_pattern,
        try_wordpress_epl_pattern,
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
