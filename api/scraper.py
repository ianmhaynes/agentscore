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
            ("sold", "/properties/sold?dateFilter=all", "SLD"),
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

                for agent in agents:
                    listings.append(Listing(
                        listing_id=str(e.get("listingId", listing_id)),
                        status=e.get("status", label),
                        address=(address.get("formatted", "") or "").replace("\n", ", "),
                        suburb=address.get("suburb", ""),
                        postcode=address.get("postCode", ""),
                        guide_price=str(e.get("price", "") or ""),
                        sold_price=str(e.get("soldPrice", "") or ""),
                        date_listed=(e.get("creationTime") or "")[:10],
                        sold_date=e.get("soldDate", "") or "",
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

        # Agent name: look for a profile link to /people/{slug} and the name
        # immediately preceding it in rendered text. Best-effort — not
        # confirmed as a stable tag structure, unlike status/address/price.
        agent_match = re.search(
            r'/people/([a-z0-9\-]+)"[^>]*>\s*(?:<[^>]+>\s*)*([A-Z][a-zA-Z\'\-]+(?:\s+[A-Z][a-zA-Z\'\-]+)+)[^\S\n]*\n',
            html,
        )
        agent_slug, agent_name = "", ""
        if agent_match:
            agent_slug = agent_match.group(1)
            agent_name = agent_match.group(2).strip()
        else:
            # Fallback: name often appears as plain text on its own line
            # right before the office line ("Harcourts Property Hub - X")
            office_line_match = re.search(
                r'^[^\S\n]*([A-Z][a-zA-Z\'\-]+\s+[A-Z][a-zA-Z\'\-]+)[^\S\n]*\n[^\S\n]*Harcourts',
                html,
                re.MULTILINE,
            )
            if office_line_match:
                agent_name = office_line_match.group(1).strip()

        # Office name: confirmed dedicated class via raw HTML inspection —
        # far more reliable than searching for the literal word "Harcourts"
        # anywhere on the page, which previously matched a font preload tag.
        office_match = re.search(
            r'<p[^>]*class="[^"]*agent-office[^"]*"[^>]*>([^<]+)</p>', html
        )
        office_name = office_match.group(1).strip() if office_match else ""

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
            date_listed="",
            sold_date="",
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


ADAPTERS = [RayWhiteDynamicsAdapter(), CloudhiRexAdapter()]


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
