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
    Regex/HTML-pattern adapter for the Cloudhi platform (observed on
    Harcourts Property Hub, propertyhub.harcourts.com.au — backed by Rex
    Software's CRM, identifiable via cloudhi.io asset domains and
    rexsoftware.com.au CDN links for floor plans).

    Unlike RayWhiteDynamicsAdapter, this platform has NO confirmed JSON
    data layer (no window.INITIAL_STATE equivalent found on inspection,
    June 2026) — listing cards are server-rendered HTML. This adapter
    parses listing cards directly off paginated index pages:
      - /listings/buy   (active)
      - /listings/sold  (sold)

    IMPORTANT CAVEATS (apply extraction_confidence="medium" everywhere):
      - Price is parsed from free-text strings ("Offers Over $979,000",
        "AUCTION", "Contact Agent") — same discard/parse rules as the
        old AgentScore Domain.com.au scraper needed. Many will be
        unparseable by design (auction/EOI listings withhold a number).
      - Sold price visibility on the /listings/sold page has NOT been
        directly confirmed at time of writing — if absent, sold_price
        will be blank for all rows from this adapter pending further
        verification.
      - Agent office is inferred from the office slug in the agent's
        profile URL, not a structured field — may be unreliable for
        multi-office agencies.
      - This is a best-effort pattern match, not a guaranteed-correct
        structured parse. Treat results as indicative, verify before
        using for anything client-facing without spot-checking.
    """

    name = "cloudhi_rex"

    def detect(self, html):
        lowered = html.lower()
        return "cloudhi.io" in lowered or "rexsoftware" in lowered

    def _parse_price(self, price_text):
        if not price_text:
            return ""
        s = price_text.strip()
        m = re.search(r"\$\s*([\d,]+)", s)
        if not m:
            return ""  # AUCTION, Contact Agent, EOI, etc. — no number to extract
        try:
            return str(int(m.group(1).replace(",", "")))
        except ValueError:
            return ""

    def _parse_cards_from_html(self, html, domain, status, source_label, log):
        listings = []
        # Find each listing card block: anchor to /listing/, headline, price, address
        # are all within a few hundred chars of each other in the rendered HTML.
        card_blocks = re.split(r'(?=<a[^>]+href="[^"]*?/listing/)', html)
        for block in card_blocks:
            url_match = re.search(r'href="(https://[^"]+/listing/[^"]+)"', block)
            if not url_match:
                continue
            listing_url = url_match.group(1)

            # Address always starts with a street/unit number directly after a
            # word boundary that is NOT preceded by a $ sign or other digits
            # (avoids matching into a preceding price string's digits, e.g.
            # "$979,000 5/13 Mapleton Circuit..." should not capture "979,000").
            addr_match = re.search(
                r'(?<![\d,$])([0-9][0-9A-Za-z\/\-]{0,8}\s+[^<>]{3,80}?,\s*'
                r'[A-Za-z][A-Za-z \'\-]+,\s*QLD\s*\d{4})',
                block,
            )
            address = addr_match.group(1).strip() if addr_match else ""
            if not address:
                continue  # can't trust this block is a real listing card without an address

            price_match = re.search(
                r'((?:Offers Over|Offers over|Sellers Committed[^<]*?|'
                r'Best Buy[^<]*?|Expressions Of Interest|Expressions of Interest)'
                r'\s*\$?[\d,]*'
                r'|AUCTION|Contact Agent'
                r'|\$[\d,]+(?:\s*-\s*\$[\d,]+)?)',
                block,
            )
            price_text = price_match.group(1).strip() if price_match else ""

            agent_match = re.search(r'people/([a-z0-9\-]+)"', block)
            agent_slug = agent_match.group(1) if agent_match else ""
            agent_name = agent_slug.replace("-", " ").title() if agent_slug else ""

            # suburb is the second comma-separated segment of the address
            suburb = ""
            parts = [p.strip() for p in address.split(",")]
            if len(parts) >= 2:
                suburb = parts[-2] if "QLD" not in parts[-2] else parts[0]

            listing_id_match = re.search(r'/listing/(r2-\d+)', listing_url, re.IGNORECASE)
            listing_id = listing_id_match.group(1) if listing_id_match else listing_url

            # Populate guide_price for active listings, sold_price for sold
            # listings — both read from the same price_text field, since this
            # platform shows asking price (active) or final price (sold) in
            # the same on-page position, just with different semantics.
            parsed_price = self._parse_price(price_text)
            guide_price = parsed_price if status == "Active" else ""
            sold_price = parsed_price if status == "Sold" else ""

            listings.append(Listing(
                listing_id=listing_id,
                status=status,
                address=address,
                suburb=suburb,
                postcode="",
                guide_price=guide_price,
                sold_price=sold_price,
                date_listed="",
                sold_date="",
                agent_name=agent_name,
                agent_email="",
                agent_phone="",
                agent_member_id=agent_slug,
                office_name="",
                office_domain=domain,
                listing_url=listing_url,
                source_adapter=self.name,
                extraction_confidence="medium",
            ))

        if not listings:
            log(f"  {source_label}: no listing cards matched (page structure may differ from expected)")
        return listings

    def fetch(self, domain, log):
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})

        all_listings = []
        seen_urls = set()

        for label, path, status in [
            ("active", "/listings/buy", "Active"),
            ("sold", "/listings/sold", "Sold"),
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

            page_listings = self._parse_cards_from_html(resp.text, domain, status, label, log)
            log(f"  {label}: parsed {len(page_listings)} listing card(s) from page 1 "
                f"(NOTE: pagination beyond page 1 not yet implemented for this adapter)")

            for l in page_listings:
                if l.listing_url not in seen_urls:
                    seen_urls.add(l.listing_url)
                    all_listings.append(l)

            time.sleep(0.5)

        return all_listings


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
