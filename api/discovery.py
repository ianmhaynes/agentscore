"""
Domain.com.au agency discovery.

Finds every real estate agency operating in a given suburb/postcode via
Domain's public agency directory, then visits each agency's individual
Domain profile page to extract their real website URL — which can then
be fed into scraper.py's existing adapters (Ray White Dynamics, Cloudhi)
the same way a manually-typed office URL would be.

IMPORTANT — NOT YET CONFIRMED LIVE:
Unlike scraper.py's adapters (each verified against real live responses
before shipping), this module's ability to reach Domain.com.au via plain
HTTP has NOT been confirmed. Domain blocked early attempts in this
project's history with a 403 (Akamai-style bot detection) when scraped
directly for listing data. Whether the *agency directory* pages specifically
are equally protected is unknown — they may be, since it's the same
domain and likely the same protection layer. Built defensively: every
failure is logged with the real HTTP status/error rather than failing
silently, so a live run will tell us definitively rather than guessing.
If this turns out to be blocked, the practical fallback is the same
two-step manual process used earlier in this project: a person finds
real office URLs themselves (search engine, looking at a known
franchise's office list) and pastes them into the existing URL box.
"""

import re
import time
from urllib.parse import urlparse
import requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20
DOMAIN_BASE = "https://www.domain.com.au"


def _slugify_suburb_postcode(raw):
    """Accepts forms like 'Mermaid Waters QLD 4218' or 'mermaid-waters-qld-4218'
    and returns the URL slug Domain expects."""
    raw = raw.strip().lower()
    if re.match(r"^[a-z0-9\-]+$", raw):
        return raw  # already slug-shaped
    raw = re.sub(r"[^a-z0-9\s]", "", raw)
    return re.sub(r"\s+", "-", raw.strip())


def _extract_agency_cards(html):
    """
    Pull agency name + Domain profile URL from a directory listing page.
    Each agency card contains a link like:
      <a href="/real-estate-agencies/{slug}-{id}/">View {Name}'s profile</a>
    """
    agencies = []
    seen_urls = set()
    for m in re.finditer(
        r'href="(/real-estate-agencies/[a-z0-9\-]+-\d+/?)"[^>]*>\s*View\s+([^\']+?)\'s profile',
        html, re.IGNORECASE,
    ):
        url = DOMAIN_BASE + m.group(1)
        name = m.group(2).strip()
        if url not in seen_urls:
            seen_urls.add(url)
            agencies.append({"name": name, "domain_profile_url": url})
    return agencies


def _extract_total_pages(html):
    """Domain shows numbered pagination links like ?page=8 — find the max."""
    pages = [int(m) for m in re.findall(r"\?page=(\d+)", html)]
    return max(pages) if pages else 1


def _extract_agency_website(html):
    """
    On an individual agency's Domain profile page, the real website is
    linked near the agency logo/header — confirmed via live inspection
    (June 2026) as a plain <a href="http://www...">link</a> immediately
    preceding the agency name heading.
    """
    m = re.search(
        r'<a href="(https?://(?!www\.domain\.com\.au)[^"]+)"[^>]*>\s*\[?\s*link',
        html, re.IGNORECASE,
    )
    if m:
        return m.group(1)
    # Fallback: any external (non-domain.com.au) link near the top of the page
    m2 = re.search(r'href="(https?://(?!.*domain\.com\.au)[^"]+)"', html)
    return m2.group(1) if m2 else None


def discover_agencies(suburb_postcode, log=print, max_pages=None):
    """
    suburb_postcode: e.g. "Mermaid Waters QLD 4218" or the slug form.
    Returns a list of dicts: {name, domain_profile_url, website} —
    website may be None if extraction failed for that agency.
    """
    slug = _slugify_suburb_postcode(suburb_postcode)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    first_url = f"{DOMAIN_BASE}/real-estate-agencies/{slug}/"
    log(f"Fetching directory page 1: {first_url}")
    try:
        resp = session.get(first_url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        log(f"ERROR: could not reach Domain directory — {e}")
        return []

    if resp.status_code != 200:
        log(f"ERROR: Domain directory returned HTTP {resp.status_code} "
            f"(likely bot detection — see scraper_discovery.py module docstring)")
        return []

    total_pages = _extract_total_pages(resp.text)
    if max_pages:
        total_pages = min(total_pages, max_pages)
    log(f"  Found {total_pages} page(s) of agencies")

    all_agencies = _extract_agency_cards(resp.text)
    log(f"  Page 1: {len(all_agencies)} agencies")

    for page_num in range(2, total_pages + 1):
        url = f"{DOMAIN_BASE}/real-estate-agencies/{slug}/?page={page_num}"
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            log(f"  Page {page_num}: ERROR fetching — {e}")
            continue
        if resp.status_code != 200:
            log(f"  Page {page_num}: HTTP {resp.status_code}, skipping")
            continue
        page_agencies = _extract_agency_cards(resp.text)
        log(f"  Page {page_num}: {len(page_agencies)} agencies")
        all_agencies.extend(page_agencies)
        time.sleep(0.5)

    log(f"Total agencies found: {len(all_agencies)}")
    log("Visiting each agency's profile page to extract their website...")

    for i, agency in enumerate(all_agencies, start=1):
        try:
            resp = session.get(agency["domain_profile_url"], timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                website = _extract_agency_website(resp.text)
                agency["website"] = website
                status = website if website else "(no website found on profile page)"
            else:
                agency["website"] = None
                status = f"HTTP {resp.status_code}"
        except requests.RequestException as e:
            agency["website"] = None
            status = f"ERROR: {e}"
        log(f"  [{i}/{len(all_agencies)}] {agency['name']}: {status}")
        time.sleep(0.3)

    found_websites = sum(1 for a in all_agencies if a.get("website"))
    log(f"Done. {found_websites} of {len(all_agencies)} agencies have a usable website URL.")
    return all_agencies
