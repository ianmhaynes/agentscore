"""
AgentScore scraping core.

Adapter-based design: each platform (Ray White Dynamics confirmed working;
others to be added once tested) implements detect + fetch + normalize.
This keeps platform-specific parsing isolated so a wrong guess about one
site's structure can never silently corrupt another site's data.
"""

import re
import json
import time
from datetime import datetime
from dataclasses import dataclass, field, asdict
from urllib.parse import urlparse
import requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

REQUEST_TIMEOUT = 20


@dataclass
class Agent:
    name: str = ""
    email: str = ""
    phone: str = ""
    member_id: str = ""
    registration_number: str = ""  # not populated yet — separate lookup needed


@dataclass
class Listing:
    listing_id: str = ""
    status: str = ""  # "active" | "sold"
    address: str = ""
    suburb: str = ""
    postcode: str = ""
    guide_price: str = ""
    sold_price: str = ""
    date_listed: str = ""
    sold_date: str = ""
    days_on_market: str = ""
    agent_name: str = ""
    agent_email: str = ""
    agent_phone: str = ""
    agent_member_id: str = ""
    office_name: str = ""
    office_domain: str = ""
    listing_url: str = ""
    source_adapter: str = ""
    extraction_confidence: str = "high"


def extract_initial_state(html):
    match = re.search(r"window\.INITIAL_STATE\s*=\s*(\{.*?\});", html, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def normalize_domain(raw_url):
    """Accepts a bare domain or full URL, returns clean https://domain."""
    raw_url = raw_url.strip()
    if not raw_url:
        return None
    if not raw_url.startswith("http"):
        raw_url = "https://" + raw_url
    parsed = urlparse(raw_url)
    return f"{parsed.scheme}://{parsed.netloc}"


def calculate_days_on_market(date_listed_str, end_date_str=None):
    """
    Calendar days from date_listed to end_date (sold_date if provided,
    otherwise today — i.e. days on market so far for an active listing).
    Accepts ISO-format date strings (YYYY-MM-DD) or the first 10 chars of
    a longer ISO timestamp. Returns "" if date_listed is missing/unparseable,
    rather than raising — a malformed date shouldn't crash a whole scrape.
    """
    if not date_listed_str:
        return ""
    try:
        start = datetime.strptime(date_listed_str[:10], "%Y-%m-%d").date()
    except ValueError:
        return ""

    if end_date_str:
        try:
            end = datetime.strptime(end_date_str[:10], "%Y-%m-%d").date()
        except ValueError:
            return ""
    else:
        end = datetime.now().date()

    delta = (end - start).days
    return str(delta) if delta >= 0 else ""  # negative would indicate bad data — don't show it as real


class RayWhiteDynamicsAdapter:
    """
    Confirmed working (manually verified June 2026) against:
      - raywhitemermaidwaters.com.au
      - raywhitesurfersparadise.com.au
    Both expose full listing data via window.INITIAL_STATE, server-rendered,
    no JS execution required. Plain HTTP GET is sufficient.
    """

    name = "ray_white_dynamics"

    def detect(self, html):
        return "window.INITIAL_STATE" in html and "dynamics.net" in html.lower() or "raywhite" in html.lower()

    def fetch(self, domain, log):
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})

        listings = []

        for label, path, status_filter in [
            ("active", "/properties/for-sale", "CUR"),
            # NOTE: deliberately NOT overriding with ?dateFilter=all here —
            # this lets the site's own default ~12-month window apply,
            # rather than pulling full multi-year history. Confirmed via
            # live testing that omitting the param yields recent sold
            # listings only (41 vs 262 for the Mermaid Waters test case).
            ("sold", "/properties/sold", "SLD"),
        ]:
            url = domain + path
            try:
                resp = session.get(url, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as e:
                log(f"  ERROR fetching {label} page: {e}")
                continue

            if resp.status_code != 200:
                log(f"  {label} page returned HTTP {resp.status_code}, skipping")
                continue

            state = extract_initial_state(resp.text)
            if not state:
                log(f"  No INITIAL_STATE found on {label} page")
                continue

            entities = state.get("listings", {}).get("entities", {})
            log(f"  {label}: found {len(entities)} entities")

            for listing_id, e in entities.items():
                if e.get("statusCode") != status_filter:
                    continue

                address = e.get("address", {}) or {}
                office = e.get("office", {}) or {}
                agents = e.get("agents", []) or [{}]

                date_listed = (e.get("creationTime") or "")[:10]
                sold_date = e.get("soldDate", "") or ""
                days_on_market = calculate_days_on_market(
                    date_listed, sold_date if sold_date else None
                )

                for agent in agents:
                    listings.append(Listing(
                        listing_id=str(e.get("listingId", listing_id)),
                        status=e.get("status", label),
                        address=(address.get("formatted", "") or "").replace("\n", ", "),
                        suburb=address.get("suburb", ""),
                        postcode=address.get("postCode", ""),
                        guide_price=str(e.get("price", "") or ""),
                        sold_price=str(e.get("soldPrice", "") or ""),
                        date_listed=date_listed,
                        sold_date=sold_date,
                        days_on_market=days_on_market,
                        agent_name=agent.get("fullName", ""),
                        agent_email=agent.get("email", ""),
                        agent_phone=agent.get("mobilePhone", ""),
                        agent_member_id=str(agent.get("memberId", "") or ""),
                        office_name=office.get("businessName", ""),
                        office_domain=domain,
                        listing_url=f"{domain}/properties/{listing_id}",
                        source_adapter=self.name,
                        extraction_confidence="high",
                    ))
            time.sleep(0.5)  # polite delay between the two page fetches

        return listings


class CloudhiRexAdapter:
    """
    Adapter for the Cloudhi platform (observed on Harcourts Property Hub,
    propertyhub.harcourts.com.au — backed by Rex Software's CRM).

    CONFIRMED STRUCTURE (verified via live DevTools inspection, June 2026)
    on individual listing detail pages:
        <p class="fw-bold mb-0">Property for Sale</p>   (or "Sold Property")
        <h1>{full address}</h1>
        <h3>{price text, e.g. "Offers Over $979,000" or "$925,000"}</h3>
        <h2 class="display-1">{headline}</h2>
    Agent name/office appear elsewhere on the page as plain text near a
    profile link to /{office-slug}/people/{agent-slug}.

    This adapter does NOT have a confirmed structured JSON data layer
    (unlike RayWhiteDynamicsAdapter) — it parses semantic HTML tags
    directly. This is more reliable than the card-splitting/regex
    approach originally attempted against the index page (which matched
    zero real listings on first live test — the index page's card
    markup differs from what was assumed). Detail-page parsing trades
    speed (one HTTP request per listing) for accuracy.

    Two-step fetch:
      1. Visit /listings/buy and /listings/sold, collect every distinct
         /listing/{slug} URL found via plain href matching (works
         regardless of card markup, since we no longer try to parse
         price/address/agent off the index page itself).
      2. Visit each listing URL individually, parse the confirmed tags.

    STILL NOT CONFIRMED / KNOWN GAPS:
      - Pagination beyond page 1 of the index pages not yet implemented.
      - Agent extraction pattern is a best-effort match against the
        rendered text near the profile link, not a tag we've directly
        confirmed in DevTools — verify before trusting agent_name fully.
      - date_listed / sold_date not present in the inspected structure.
      - Visiting every listing individually means this is much slower
        than the Ray White adapter (N+2 requests vs 2) — for offices
        with hundreds of sold listings this could be slow or hit
        Vercel's function timeout. Worth capping per-office listing
        count if this becomes a problem in practice.
    """

    name = "cloudhi_rex"

    def detect(self, html):
        lowered = html.lower()
        return "cloudhi.io" in lowered or "rexsoftware" in lowered

    def _parse_price(self, price_text):
        if not price_text:
            return ""
        m = re.search(r"\$\s*([\d,]+)", price_text)
        if not m:
            return ""  # AUCTION, Contact Agent, EOI, etc. — no number to extract
        try:
            return str(int(m.group(1).replace(",", "")))
        except ValueError:
            return ""

    def _parse_human_date(self, date_text):
        """Convert '17 June, 2026' or '17 June 2026' -> '2026-06-17'.
        Returns '' on any parse failure rather than raising."""
        if not date_text:
            return ""
        cleaned = date_text.strip().replace(",", "")
        try:
            return datetime.strptime(cleaned, "%d %B %Y").strftime("%Y-%m-%d")
        except ValueError:
            return ""

    def _collect_listing_urls(self, html, domain):
        urls = set()
        for m in re.finditer(r'href="(https://[^"]+/listing/[^"\s]+)"', html):
            urls.add(m.group(1))
        # Also catch relative-path hrefs just in case
        for m in re.finditer(r'href="(/listing/[^"\s]+)"', html):
            urls.add(domain + m.group(1))
        return urls

    def _parse_detail_page(self, html, listing_url, domain, log):
        status_match = re.search(
            r'<p[^>]*class="[^"]*fw-bold[^"]*"[^>]*>\s*(Property for Sale|Sold Property)\s*</p>',
            html,
        )
        if not status_match:
            log(f"    No status label found on {listing_url}, skipping")
            return None
        status = "Active" if status_match.group(1) == "Property for Sale" else "Sold"

        addr_match = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
        address = addr_match.group(1).strip() if addr_match else ""
        if not address:
            log(f"    No address (h1) found on {listing_url}, skipping")
            return None

        # Price lives in a bare <h3> with NO class attribute — confirmed via
        # raw HTML inspection that the page has several other <h3> tags with
        # classes (e.g. class="display-1 mb-0" repeats the address) that
        # would otherwise be matched first by a generic <h3> search.
        price_match = re.search(r'<h3>([^<]+)</h3>', html)
        price_text = price_match.group(1).strip() if price_match else ""
        parsed_price = self._parse_price(price_text)

        # Agent name: confirmed dedicated class via raw HTML inspection —
        # sibling to the agent-office class fixed above. Far more reliable
        # than the previous approach of matching plain text near the
        # /people/{slug} profile link, which broke because the real link
        # is immediately followed by an <img> tag and a data-agentname
        # attribute (a slugified, lowercase value), not visible name text.
        agent_name_match = re.search(
            r'<p[^>]*class="[^"]*agent-name[^"]*"[^>]*>([^<]+)</p>', html
        )
        agent_name = agent_name_match.group(1).strip() if agent_name_match else ""

        agent_slug_match = re.search(r'/people/([a-z0-9\-]+)"', html)
        agent_slug = agent_slug_match.group(1) if agent_slug_match else ""

        # Office name: confirmed dedicated class via raw HTML inspection —
        # far more reliable than searching for the literal word "Harcourts"
        # anywhere on the page, which previously matched a font preload tag.
        office_match = re.search(
            r'<p[^>]*class="[^"]*agent-office[^"]*"[^>]*>([^<]+)</p>', html
        )
        office_name = office_match.group(1).strip() if office_match else ""

        # Dates — confirmed via raw HTML inspection of one active and one
        # sold listing (June 2026):
        #   Active: plain text "Added 17 June, 2026" near Property ID
        #   Sold:   <h3>Sold Date</h3> ... <div class="col">16 June, 2026</div>
        date_listed = ""
        added_match = re.search(r'Added\s+(\d{1,2}\s+\w+,?\s+\d{4})', html)
        if added_match:
            date_listed = self._parse_human_date(added_match.group(1))

        sold_date = ""
        if status == "Sold":
            sold_date_match = re.search(
                r'Sold Date</h3>.*?<div[^>]*class="col"[^>]*>([^<]+)</div>',
                html, re.DOTALL,
            )
            if sold_date_match:
                sold_date = self._parse_human_date(sold_date_match.group(1))

        days_on_market = calculate_days_on_market(
            date_listed, sold_date if sold_date else None
        ) if date_listed else ""

        # suburb: second-to-last comma-separated segment of the address
        suburb = ""
        parts = [p.strip() for p in address.split(",")]
        if len(parts) >= 2:
            suburb = parts[-2]

        listing_id_match = re.search(r'/listing/(r2-\d+)', listing_url, re.IGNORECASE)
        listing_id = listing_id_match.group(1) if listing_id_match else listing_url

        return Listing(
            listing_id=listing_id,
            status=status,
            address=address,
            suburb=suburb,
            postcode="",
            guide_price=parsed_price if status == "Active" else "",
            sold_price=parsed_price if status == "Sold" else "",
            date_listed=date_listed,
            sold_date=sold_date,
            days_on_market=days_on_market,
            agent_name=agent_name,
            agent_email="",
            agent_phone="",
            agent_member_id=agent_slug,
            office_name=office_name,
            office_domain=domain,
            listing_url=listing_url,
            source_adapter=self.name,
            extraction_confidence="medium",
        )

    def fetch(self, domain, log):
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})

        listing_urls = set()
        for label, path in [("active", "/listings/buy"), ("sold", "/listings/sold")]:
            url = domain + path
            try:
                resp = session.get(url, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as e:
                log(f"  ERROR fetching {label} index page: {e}")
                continue
            if resp.status_code != 200:
                log(f"  {label} index page returned HTTP {resp.status_code}, skipping")
                continue
            found = self._collect_listing_urls(resp.text, domain)
            log(f"  {label} index: found {len(found)} listing URL(s) on page 1 "
                f"(pagination not yet implemented for this adapter)")
            listing_urls.update(found)
            time.sleep(0.5)

        log(f"  Visiting {len(listing_urls)} individual listing page(s) for detail data...")
        listings = []
        for listing_url in listing_urls:
            try:
                resp = session.get(listing_url, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as e:
                log(f"    ERROR fetching {listing_url}: {e}")
                continue
            if resp.status_code != 200:
                log(f"    {listing_url} returned HTTP {resp.status_code}, skipping")
                continue
            parsed = self._parse_detail_page(resp.text, listing_url, domain, log)
            if parsed:
                listings.append(parsed)
            time.sleep(0.3)

        log(f"  Parsed {len(listings)} of {len(listing_urls)} listing page(s) successfully")
        return listings


class LJHookerAdapter:
    """
    Adapter for one of LJ Hooker's website platform generations — the one
    serving individual listing pages from property.ljhooker.com.au with
    Schema.org structured markup, confirmed via live DevTools inspection
    of a real Pyrmont (NSW) listing (June 2026):

        <section class="property-overview container--section"
                 itemscope itemtype="https://schema.org/IndividualProduct">
          <h2 class="property-overview__address" itemprop="name">{address}</h2>
          <p id="property-information" class="property-overview__status"
             itemprop="identifier">Sold For $1,670,000</p>
          ...
        </section>

    The single itemprop="identifier" field gives BOTH status and price in
    one confirmed string ("Sold For $X" / "For Sale $X" or similar) — more
    reliable than the separate URL-path or decoy-tag-avoidance tricks the
    other adapters needed.

    IMPORTANT — LJ Hooker runs MULTIPLE distinct website platforms across
    its national network. A separate office (Broadbeach, QLD) was
    confirmed via live inspection to run a different, HubSpot-powered
    site (broadbeach.ljhooker.com.au) where listing data loads via
    client-side JavaScript and is NOT present in the plain HTML response
    at all — that platform is NOT covered by this adapter, and detecting
    it would require a real browser, which this project does not use.
    This adapter only covers offices on the property.ljhooker.com.au-style
    platform. Real, but partial, coverage — same as every adapter in this
    file covers what it covers and nothing more.

    Listing index pages use an explicit, unambiguous query parameter
    (more reliable than Belle's URL-path convention or a text label):
        ljhooker.com.au/residential-search-results?officeId={id}&searchProfile=sold
        ljhooker.com.au/residential-search-results?officeId={id}&searchProfile=buy
    officeId must be discovered from a specific office's own site (it
    appears in that office's own internal links, e.g. footer "View Our
    Recent Sales" link) — there is no known way to enumerate all LJ Hooker
    office IDs without visiting each office's site individually first.
    """

    name = "lj_hooker"

    def detect(self, html):
        # Distinguishing marker: this platform's pages reference
        # property.ljhooker.com.au or carry the confirmed itemprop
        # scaffolding; the other known LJ Hooker platform (HubSpot) does
        # not have either of these markers in its raw HTML.
        lowered = html.lower()
        return "property.ljhooker.com.au" in lowered or (
            "ljhooker" in lowered and 'itemprop="identifier"' in lowered
        )

    def _parse_price_and_status(self, status_price_text):
        """'Sold For $1,670,000' -> ('Sold', '1670000'). Also handles
        'For Sale $X', 'Offers Over $X', etc. — same discard rules as
        other adapters for non-numeric price text (Contact Agent, etc.)."""
        if not status_price_text:
            return "", ""
        text = status_price_text.strip()
        is_sold = text.lower().startswith("sold")
        status = "Sold" if is_sold else "Active"

        price_match = re.search(r"\$\s*([\d,]+)", text)
        price = ""
        if price_match:
            try:
                price = str(int(price_match.group(1).replace(",", "")))
            except ValueError:
                price = ""
        return status, price

    def _collect_listing_urls(self, html, domain):
        urls = set()
        for m in re.finditer(r'href="(https://property\.ljhooker\.com\.au/[^"\s]+)"', html):
            urls.add(m.group(1))
        return urls

    def _parse_detail_page(self, html, listing_url, domain, log):
        status_price_match = re.search(
            r'itemprop="identifier"[^>]*>([^<]+)<', html
        )
        if not status_price_match:
            # Fallback: the confirmed visible text pattern even if the
            # exact itemprop attribute ordering differs from what's expected
            status_price_match = re.search(
                r'>((?:Sold|For Sale)\s+For\s+\$[\d,]+|(?:Sold|For Sale)[^<]{0,40}\$[\d,]+)<',
                html,
            )
        if not status_price_match:
            log(f"    No status/price field found on {listing_url}, skipping")
            return None

        status, price = self._parse_price_and_status(status_price_match.group(1))

        addr_match = re.search(r'itemprop="name"[^>]*>([^<]+)<', html)
        if not addr_match:
            addr_match = re.search(
                r'<h2[^>]*class="[^"]*property-overview__address[^"]*"[^>]*>([^<]+)</h2>',
                html,
            )
        address = addr_match.group(1).strip() if addr_match else ""
        if not address:
            log(f"    No address found on {listing_url}, skipping")
            return None

        # Agent: confirmed pattern "Contact: {Name} {Phone}" in the
        # description area, plus a separate structured agent-profile link
        # (e.g. agent.ljhooker.com.au/{slug}) in the listing banner.
        agent_name, agent_phone = "", ""
        contact_match = re.search(
            r"Contact:\s*([A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+)+)\s+(\d[\d\s]{7,})",
            html,
        )
        if contact_match:
            agent_name = contact_match.group(1).strip()
            agent_phone = re.sub(r"\s+", " ", contact_match.group(2).strip())
        else:
            # Fallback: agent.ljhooker.com.au profile link text
            agent_link_match = re.search(
                r'agent\.ljhooker\.com\.au/[a-z0-9\-]+"[^>]*>([^<]+)<', html
            )
            if agent_link_match:
                agent_name = agent_link_match.group(1).strip()

        suburb = ""
        parts = [p.strip() for p in address.split(",")]
        if len(parts) >= 2:
            # last segment is often "SUBURB STATE" combined — take the
            # second-to-last comma-separated piece if it looks state-free
            suburb_candidate = parts[-1]
            suburb_match = re.match(r"([A-Za-z\s]+?)\s+[A-Z]{2,3}$", suburb_candidate)
            suburb = suburb_match.group(1).strip() if suburb_match else ""

        listing_id_match = re.search(r"-([a-z0-9]{6,8})$", listing_url)
        listing_id = listing_id_match.group(1) if listing_id_match else listing_url

        return Listing(
            listing_id=listing_id,
            status=status,
            address=address,
            suburb=suburb,
            postcode="",
            guide_price=price if status == "Active" else "",
            sold_price=price if status == "Sold" else "",
            date_listed="",
            sold_date="",
            agent_name=agent_name,
            agent_email="",
            agent_phone=agent_phone,
            agent_member_id="",
            office_name="",
            office_domain=domain,
            listing_url=listing_url,
            source_adapter=self.name,
            extraction_confidence="medium",
        )

    def fetch(self, domain, log):
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})

        # officeId is office-specific and not derivable from the bare
        # domain alone — search the homepage for a self-referencing
        # search-results link that reveals this office's own officeId,
        # the same way the user found it by inspecting their own site.
        try:
            resp = session.get(domain, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            log(f"  ERROR fetching homepage: {e}")
            return []

        office_id_match = re.search(r"officeId=(\d+)", resp.text)
        if not office_id_match:
            log("  Could not find officeId on homepage — this office's site "
                "structure may differ from the confirmed pattern")
            return []
        office_id = office_id_match.group(1)
        log(f"  Found officeId={office_id}")

        listing_urls = set()
        for label, profile in [("active", "buy"), ("sold", "sold")]:
            search_url = (
                f"https://www.ljhooker.com.au/residential-search-results"
                f"?officeId={office_id}&orderBy=date-desc&searchProfile={profile}"
            )
            try:
                resp = session.get(search_url, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as e:
                log(f"  ERROR fetching {label} index: {e}")
                continue
            if resp.status_code != 200:
                log(f"  {label} index returned HTTP {resp.status_code}")
                continue
            found = self._collect_listing_urls(resp.text, domain)
            log(f"  {label} index: found {len(found)} listing URL(s)")
            listing_urls.update(found)
            time.sleep(0.5)

        if not listing_urls:
            log("  No listing URLs found via search-results pages")
            return []

        log(f"  Visiting {len(listing_urls)} listing page(s)...")
        listings = []
        for listing_url in listing_urls:
            try:
                resp = session.get(listing_url, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as e:
                log(f"    ERROR fetching {listing_url}: {e}")
                continue
            if resp.status_code != 200:
                continue
            parsed = self._parse_detail_page(resp.text, listing_url, domain, log)
            if parsed:
                listings.append(parsed)
            time.sleep(0.3)

        log(f"  Parsed {len(listings)} of {len(listing_urls)} listing page(s) successfully")
        return listings


class GenericFallbackAdapter:
    """
    Last-resort adapter for any site that doesn't match a known platform
    (Ray White Dynamics, Harcourts/Cloudhi). Built from live inspection of
    Belle Property (June 2026), but designed to be tried broadly across
    any agency site as a best-effort catch-all — NOT a precise,
    confirmed-structure adapter the way the other two are.

    CONFIRMED (via Belle Property DevTools inspection):
      - Price: <div class="price">Offers from $795,000</div> (active) or
        <div class="price">$1,335,000</div> (sold) — same class either way.
      - Address: <h1 class="address">...</h1>
      - Agent info: <section class="property-agents">...</section>
      - Sold status is signalled by the LISTING URL itself living under a
        path segment like /sold/ — not by text on the page. This is a
        genuinely different signal from Ray White (status field in JSON)
        and Harcourts (a text label on the page), and the user's own
        confirmation that this generalizes ("they all follow the same
        general plan, search for price/sold/$") is the basis for trying
        this broadly rather than treating it as Belle-specific.

    NOT CONFIRMED for any site other than Belle:
      - Whether other agencies actually use class="price"/class="address"
        (likely NOT universal — this is almost certainly a per-CMS
        convention, same as Cloudhi's agent-name/agent-office classes
        were specific to that one platform). The fallback regexes below
        exist specifically for sites where the exact class names don't
        match, but a generic "find $ near digits" pattern is inherently
        higher-risk for false positives (matching an unrelated price
        mentioned in page copy, a related-listings carousel, etc.) than
        every other adapter in this file.
      - Whether a /sold/ URL segment is a universal convention or
        Belle-specific. Sites without it will have ALL their listings
        come back as "Active" even if some are actually sold — a real,
        known limitation, not silently corrected.

    Always marked extraction_confidence="low" — one tier below Cloudhi's
    "medium" — so scoring and the UI can treat this data with
    appropriately heavier skepticism than even the pattern-matched
    Harcourts adapter.
    """

    name = "generic_fallback"

    # Tried in order against the homepage to find a listing index page.
    # Drawn from conventions confirmed across Ray White, Harcourts, and
    # Belle Property's own "Properties for sale" / "Recently sold" menu
    # items — NOT confirmed universal beyond those three.
    CANDIDATE_INDEX_PATHS = [
        "/buy", "/properties/for-sale", "/properties-for-sale",
        "/for-sale", "/listings/buy", "/sell/recently-sold",
        "/recently-sold", "/sold",
    ]

    def detect(self, html):
        # Deliberately always matches — this is the catch-all, tried only
        # after every more specific adapter has already had a chance to
        # claim the site. Order in ADAPTERS is what makes this safe.
        return True

    def _looks_like_listing_url(self, url, domain):
        if not url.startswith(domain):
            return False
        # crude heuristic: a property URL is usually a deep path with
        # several hyphenated words (an address) — short paths are more
        # likely to be nav links, not listings
        path = url[len(domain):]
        return path.count("-") >= 2 and len(path) > 20

    def _collect_listing_urls(self, html, domain):
        urls = set()
        for m in re.finditer(r'href="(https?://[^"\s]+)"', html):
            url = m.group(1)
            if self._looks_like_listing_url(url, domain):
                urls.add(url)
        for m in re.finditer(r'href="(/[^"\s]+)"', html):
            url = domain + m.group(1)
            if self._looks_like_listing_url(url, domain):
                urls.add(url)
        return urls

    def _parse_price(self, price_text):
        if not price_text:
            return ""
        m = re.search(r"\$\s*([\d,]+)", price_text)
        if not m:
            return ""
        try:
            return str(int(m.group(1).replace(",", "")))
        except ValueError:
            return ""

    def _parse_detail_page(self, html, listing_url, domain, log):
        # Status from URL path, NOT page text — confirmed approach for
        # Belle Property. Sites that don't use a /sold/-style path will
        # never report Sold via this adapter (known limitation, logged
        # in the docstring, not hidden).
        is_sold = bool(re.search(r"/sold/|/sold-", listing_url, re.IGNORECASE))
        status = "Sold" if is_sold else "Active"

        # Price: confirmed class="price" first, generic fallback second.
        price_match = re.search(r'<div[^>]*class="[^"]*\bprice\b[^"]*"[^>]*>([^<]+)</div>', html)
        if not price_match:
            # Generic fallback: any element whose text is mostly a $ amount
            price_match = re.search(r'>([^<]{0,40}\$[\d,]{4,}[^<]{0,20})<', html)
        price_text = price_match.group(1).strip() if price_match else ""
        parsed_price = self._parse_price(price_text)

        # Address: confirmed class="address" first, generic fallback to h1.
        addr_match = re.search(r'<h1[^>]*class="[^"]*\baddress\b[^"]*"[^>]*>([^<]+)</h1>', html)
        if not addr_match:
            addr_match = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
        address = addr_match.group(1).strip() if addr_match else ""
        if not address:
            log(f"    No address found on {listing_url}, skipping (too unreliable without one)")
            return None

        # Agent info: confirmed section class="property-agents" — pull
        # the first plausible name-shaped text inside it. Generic and
        # best-effort; no fallback if this section is absent, since
        # guessing agent names from unrelated text is too risky even for
        # this already-low-confidence adapter.
        agent_name = ""
        agents_section = re.search(
            r'<section[^>]*class="[^"]*property-agents[^"]*"[^>]*>(.*?)</section>',
            html, re.DOTALL,
        )
        if agents_section:
            name_match = re.search(
                r'>([A-Z][a-zA-Z\'\-]+(?:\s+[A-Z][a-zA-Z\'\-]+)+)<',
                agents_section.group(1),
            )
            if name_match:
                agent_name = name_match.group(1).strip()

        suburb = ""
        parts = [p.strip() for p in address.split(",")]
        if len(parts) >= 2:
            suburb = parts[-2] if not re.search(r"\d", parts[-2]) else ""

        return Listing(
            listing_id=listing_url,
            status=status,
            address=address,
            suburb=suburb,
            postcode="",
            guide_price=parsed_price if status == "Active" else "",
            sold_price=parsed_price if status == "Sold" else "",
            date_listed="",
            sold_date="",
            agent_name=agent_name,
            agent_email="",
            agent_phone="",
            agent_member_id="",
            office_name="",
            office_domain=domain,
            listing_url=listing_url,
            source_adapter=self.name,
            extraction_confidence="low",
        )

    def fetch(self, domain, log):
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})

        listing_urls = set()
        for path in self.CANDIDATE_INDEX_PATHS:
            url = domain + path
            try:
                resp = session.get(url, timeout=REQUEST_TIMEOUT)
            except requests.RequestException:
                continue
            if resp.status_code != 200:
                continue
            found = self._collect_listing_urls(resp.text, domain)
            if found:
                log(f"  {path}: found {len(found)} candidate listing URL(s)")
                listing_urls.update(found)
            time.sleep(0.3)

        if not listing_urls:
            log("  No listing index page matched any known path pattern — "
                "this site's structure is not covered by the generic fallback")
            return []

        log(f"  Visiting {len(listing_urls)} candidate listing page(s)...")
        listings = []
        for listing_url in listing_urls:
            try:
                resp = session.get(listing_url, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as e:
                log(f"    ERROR fetching {listing_url}: {e}")
                continue
            if resp.status_code != 200:
                continue
            parsed = self._parse_detail_page(resp.text, listing_url, domain, log)
            if parsed:
                listings.append(parsed)
            time.sleep(0.2)

        log(f"  Parsed {len(listings)} of {len(listing_urls)} candidate page(s) successfully "
            f"(extraction_confidence=low — verify before relying on this data)")
        return listings


ADAPTERS = [RayWhiteDynamicsAdapter(), CloudhiRexAdapter(), LJHookerAdapter(), GenericFallbackAdapter()]


def scrape_office(raw_url, log=print):
    """
    Scrape a single office. Tries each adapter's detect() against the
    homepage HTML; uses the first that matches. Returns (listings, error).
    If no adapter matches, returns ([], reason) rather than guessing.
    """
    domain = normalize_domain(raw_url)
    if not domain:
        return [], "Could not parse URL"

    log(f"Checking {domain} ...")
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        resp = session.get(domain, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        return [], f"Could not reach site: {e}"

    if resp.status_code != 200:
        return [], f"Site returned HTTP {resp.status_code}"

    matched_adapter = None
    for adapter in ADAPTERS:
        if adapter.detect(resp.text):
            matched_adapter = adapter
            break

    if not matched_adapter:
        return [], "No known platform detected for this site (not yet supported)"

    log(f"  Matched adapter: {matched_adapter.name}")
    listings = matched_adapter.fetch(domain, log)
    return listings, None


def scrape_offices(urls, log=print):
    """Scrape a list of office URLs. Returns dict with results + per-office status."""
    all_listings = []
    office_results = []

    for raw_url in urls:
        raw_url = raw_url.strip()
        if not raw_url:
            continue
        listings, error = scrape_office(raw_url, log=log)
        office_results.append({
            "url": raw_url,
            "success": error is None,
            "error": error,
            "listing_count": len(listings),
        })
        all_listings.extend(listings)

    return {
        "listings": [asdict(l) for l in all_listings],
        "office_results": office_results,
    }
