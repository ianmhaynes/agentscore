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


ADAPTERS = [RayWhiteDynamicsAdapter()]


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
