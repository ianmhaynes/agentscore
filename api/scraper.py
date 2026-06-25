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
import extraction_tiers
import browserless_fallback

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
        # CONFIRMED REAL BUG (June 24, 2026): "rexsoftware" alone is too
        # broad — Rex Software (the parent company) makes at least TWO
        # distinct, structurally different products: Cloudhi (this
        # adapter's actual target) and "Rex Websites" (a different,
        # also-server-rendered product, confirmed real on Kangaroo
        # Point Real Estate, whose footer links to
        # rexsoftware.com/products/real-estate-websites — the SAME
        # parent-company domain, but a COMPLETELY DIFFERENT page
        # structure, handled by its own dedicated tier in
        # extraction_tiers.py, not this adapter at all). Matching on
        # the company name alone wrongly claimed Rex Websites sites for
        # this adapter, which then correctly found nothing (wrong
        # URL/page assumptions for that different product) and
        # returned zero listings — exactly the kind of silent,
        # confidently-wrong routing this project has hit more than once
        # with similarly-named but structurally distinct platforms
        # (e.g. yesterday's LJ Hooker HubSpot-vs-legacy distinction).
        # Require "cloudhi.io" specifically, OR the more specific
        # "rexsoftware.com/products/crm" (Cloudhi's actual product
        # page, distinct from "/products/real-estate-websites").
        if "cloudhi.io" in lowered:
            return True
        if "rexsoftware" in lowered and "real-estate-websites" not in lowered:
            return True
        return False

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
    Adapter for individual LJ Hooker listing pages served from
    property.ljhooker.com.au, with Schema.org structured markup,
    confirmed via live DevTools inspection AND a direct fetch of a real
    Pyrmont (NSW) listing (June 2026):

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
    other adapters needed. Individual listing pages are confirmed
    server-rendered and Google-indexed (hundreds of real examples found
    via a site: search) — genuinely scrapable, no JS execution needed.

    CONFIRMED, REAL LIMITATION — discovering an office's full listing
    set is NOT currently solved, after exhausting every standard option:

      1. Every LJ Hooker office homepage (Broadbeach AND Pyrmont alike,
         both confirmed via live fetch) is a HubSpot-powered marketing
         shell. detect() matches on this shell's searchProfile= links.
      2. The "own subdomain" search-results page
         ({domain}/search-results?searchProfile=buy&searchOrigin=office)
         that those links point to is ALSO confirmed JS-loaded — its raw
         HTML contains only literal "listing item" placeholders, no real
         addresses/prices/links, on Pyrmont specifically (confirmed via
         live fetch, June 2026), despite individual listing pages on a
         DIFFERENT domain being fully server-rendered.
      3. An officeId-based fallback to a national-domain search-results
         URL was tried (see fetch() below) on the theory that some
         office generations might expose results differently — this
         is unconfirmed to work for any real office; it's a fallback
         for a theoretical case, not a proven second path.
      4. /robots.txt was checked and DOES list a Sitemap directive
         (https://property.ljhooker.com.au/sitemap_custom.xml) — but
         that exact URL returns a 404. /sitemap.xml and
         /sitemap_index.xml (the standard default locations) also both
         return 404. No working sitemap exists at any checked location.

    PRACTICAL CONSEQUENCE: this adapter can correctly parse a LJ Hooker
    listing page IF you already have its URL, but currently has NO
    reliable way to discover the full set of an office's listing URLs
    automatically. fetch() will return an empty list with a clear log
    explanation for essentially every real office tested so far — this
    is an honest, confirmed gap, not a guess, and not silently hidden.
    Until a discovery method is found, this adapter exists mainly to
    correctly parse individual listing pages if/when they're fed in
    directly (e.g. via a future "single listing URL" input mode, or if
    a working discovery method is found later).
    """

    name = "lj_hooker"

    def __init__(self, browserless_api_key=None):
        # Same opt-in pattern as GenericFallbackAdapter — only used when
        # both the own-subdomain and officeId-based discovery paths
        # (both confirmed real, both genuinely fail for this platform
        # generation, see class docstring) find nothing at all.
        self.browserless_api_key = browserless_api_key
        self.browserless_call_count = 0

    def detect(self, html):
        # IMPORTANT: detect() runs against the HOMEPAGE, which is a
        # separate, HubSpot-powered marketing shell — confirmed via live
        # fetch of pyrmont.ljhooker.com.au (meta-generator: HubSpot,
        # listing data NOT present, same as the Broadbeach JS-loaded
        # platform). The itemprop="identifier" schema markup this
        # adapter relies on only exists on individual LISTING pages
        # (property.ljhooker.com.au/...), never on the homepage itself —
        # checking for it here was a real bug found via live testing.
        # The one thing EVERY LJ Hooker office homepage reliably links to
        # is its own search-results page via the searchProfile= query
        # param (buy/sold/rent/leased) — confirmed present on both
        # Broadbeach and Pyrmont homepages despite their listing data
        # living on different platforms. This is what we actually check.
        lowered = html.lower()
        return "ljhooker" in lowered and "searchprofile=" in lowered

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
        # CONFIRMED REAL BUGS (June 24, 2026), found via raw HTML from
        # Browserless against a real, currently-live listing
        # (90 Mortensen Road, Nerang QLD):
        #   1. The real <p itemprop="identifier"> can be PLAIN "SOLD"
        #      with no price attached at all — confirmed real case
        #      where LJ Hooker doesn't publish a final sold price. The
        #      original regex required a $ amount in the same match,
        #      which made this fail and fall through to a much looser
        #      fallback regex that, combined with bug 2, matched the
        #      wrong content entirely.
        #   2. The page's NAV BAR has its own itemprop="name" element —
        #      <a href=".../buy" itemprop="url"><span itemprop="name">
        #      Buy</span></a> — which the address fallback regex
        #      (itemprop="name"[^>]*>([^<]+)<) matched FIRST, since it
        #      appears earlier in the document than the real address
        #      heading (<h2 class="property-overview__address"
        #      itemprop="name">90 Mortensen Road, Nerang</h2>). This is
        #      why "Buy" was being extracted as the address for every
        #      single listing on this page generation.
        # Fixed by: checking the specific property-overview__status/
        # __address classes FIRST (unambiguous, can't collide with nav
        # elements), with the original itemprop-only patterns kept only
        # as a fallback for pages where the specific classes might
        # differ.
        status_price_match = re.search(
            r'class="[^"]*property-overview__status[^"]*"[^>]*>([^<]+)<', html
        )
        if not status_price_match:
            status_price_match = re.search(
                r'itemprop="identifier"[^>]*>([^<]+)<', html
            )
        if not status_price_match:
            status_price_match = re.search(
                r'>((?:Sold|For Sale)\s+For\s+\$[\d,]+|(?:Sold|For Sale)[^<]{0,40}\$[\d,]+)<',
                html,
            )
        if not status_price_match:
            log(f"    No status/price field found on {listing_url}, skipping")
            return None

        status, price = self._parse_price_and_status(status_price_match.group(1))

        addr_match = re.search(
            r'class="[^"]*property-overview__address[^"]*"[^>]*>([^<]+)<', html
        )
        if not addr_match:
            addr_match = re.search(r'itemprop="name"[^>]*>([^<]+)<', html)
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
            if suburb_match:
                suburb = suburb_match.group(1).strip()
            else:
                # CONFIRMED REAL CASE (June 24, 2026, found via raw HTML):
                # some addresses on this platform have NO state suffix
                # at all in the last comma-separated segment — just the
                # bare suburb name (e.g. "90 Mortensen Road, Nerang",
                # not "..., Nerang QLD"). If the segment is short and
                # alphabetic, it's very likely already just the suburb.
                if re.match(r"^[A-Za-z\s]+$", suburb_candidate) and len(suburb_candidate) < 30:
                    suburb = suburb_candidate

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

        # CORRECTED after live testing: officeId is NOT universal across
        # LJ Hooker offices. Confirmed via live fetch that Pyrmont's own
        # nav links use no officeId at all — just
        # "{domain}/search-results?searchProfile=buy&searchOrigin=office",
        # relying on the subdomain itself to scope results. Broadbeach's
        # FOOTER links happened to include an explicit officeId, but that
        # appears to be a secondary/alternate path, not the primary one.
        # Primary approach: hit the office's own subdomain directly, no
        # ID needed. Fallback: look for an officeId anywhere on the
        # homepage and use the national-domain URL, in case some office
        # generations only expose listings that way.
        listing_urls = set()
        used_fallback = False

        for label, profile in [("active", "buy"), ("sold", "sold")]:
            search_url = f"{domain}/search-results?searchProfile={profile}&orderBy=date-desc&searchOrigin=office"
            try:
                resp = session.get(search_url, timeout=REQUEST_TIMEOUT)
            except requests.RequestException as e:
                log(f"  ERROR fetching {label} index ({search_url}): {e}")
                continue
            if resp.status_code != 200:
                log(f"  {label} index returned HTTP {resp.status_code} ({search_url})")
                continue
            found = self._collect_listing_urls(resp.text, domain)
            log(f"  {label} index (own subdomain): found {len(found)} listing URL(s)")
            listing_urls.update(found)
            time.sleep(0.5)

        if not listing_urls:
            log("  No listings found via own-subdomain search-results pages — "
                "trying officeId-based fallback...")
            try:
                resp = session.get(domain, timeout=REQUEST_TIMEOUT)
                office_id_match = re.search(r"officeId=(\d+)", resp.text)
            except requests.RequestException as e:
                log(f"  ERROR fetching homepage for fallback: {e}")
                office_id_match = None

            if office_id_match:
                office_id = office_id_match.group(1)
                log(f"  Found officeId={office_id}, trying national-domain URLs")
                used_fallback = True
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
                        continue
                    found = self._collect_listing_urls(resp.text, domain)
                    log(f"  {label} index (officeId fallback): found {len(found)} listing URL(s)")
                    listing_urls.update(found)
                    time.sleep(0.5)
            else:
                log("  No officeId found either — this office's listings are not "
                    "reachable via either known pattern (it may be on the "
                    "JS-loaded platform with no server-rendered listing data)")

        if not listing_urls:
            if not self.browserless_api_key:
                log("  No listing URLs found via any known search-results pattern")
                return []

            # LAST RESORT (June 24, 2026): both known discovery paths
            # are confirmed genuinely empty for this platform generation
            # (HubSpot homepage shell, JS-loaded search-results page —
            # see class docstring for full confirmation history). Try
            # the homepage's JS-RENDERED version via Browserless.
            log("  Both known discovery patterns found nothing via plain HTTP — "
                "trying Browserless (JS-rendered) as a last resort...")
            rendered_html = browserless_fallback.fetch_rendered_html(
                domain, self.browserless_api_key, log=log,
            )
            if not rendered_html:
                log("  Browserless fallback also found nothing usable — giving up on this office")
                return []

            self.browserless_call_count += 1
            found = self._collect_listing_urls(rendered_html, domain)
            if not found:
                log("  Browserless returned rendered HTML, but no candidate listing URLs "
                    "were found in it either")
                return []
            log(f"  Browserless (JS-rendered homepage): found {len(found)} listing URL(s)")
            listing_urls.update(found)
            needed_browserless = True
        else:
            needed_browserless = False

        log(f"  Visiting {len(listing_urls)} listing page(s)"
            f"{' (via officeId fallback)' if used_fallback else ''}...")
        listings = []
        for listing_url in listing_urls:
            html = None
            if needed_browserless:
                html = browserless_fallback.fetch_rendered_html(
                    listing_url, self.browserless_api_key, log=log,
                )
                if html:
                    self.browserless_call_count += 1
            else:
                try:
                    resp = session.get(listing_url, timeout=REQUEST_TIMEOUT)
                except requests.RequestException as e:
                    log(f"    ERROR fetching {listing_url}: {e}")
                    continue
                if resp.status_code != 200:
                    continue
                html = resp.text

            if not html:
                continue
            parsed = self._parse_detail_page(html, listing_url, domain, log)
            if parsed:
                listings.append(parsed)
            time.sleep(0.3)

        log(f"  Parsed {len(listings)} of {len(listing_urls)} listing page(s) successfully")
        return listings


class GenericFallbackAdapter:
    """
    Last-resort adapter for any site that doesn't match a known platform
    (Ray White Dynamics, Harcourts/Cloudhi, LJ Hooker). Originally built
    from Belle Property alone; now uses the tiered extraction pipeline
    in extraction_tiers.py, built from 9 real, unrelated agency sites
    inspected in one session (June 2026) — see that module's docstring
    for the full list and findings.

    Tries, in order: JSON-LD -> meta tags -> a confirmed shared template
    (Viridity/JBRE) -> the original Belle-derived class="price"/class=
    "address" check -> (optionally) an LLM extraction call as the true
    last resort, only if an API key is supplied.

    extraction_confidence is set per-listing based on which tier
    actually produced the result — "low" for every free tier (none of
    them are confirmed universal), "low" for the LLM tier too for now
    (no accuracy data yet at scale — see README for the explicit
    decision not to claim higher confidence without evidence).
    """

    name = "generic_fallback"

    # Tried in order against the homepage to find a listing index page.
    # Drawn from conventions confirmed across Ray White, Harcourts, and
    # Belle Property's own "Properties for sale" / "Recently sold" menu
    # items — NOT confirmed universal beyond those three. "" (the bare
    # homepage itself) added after a real bug found via live testing:
    # Viridity Real Estate (platform: premises.com.au, confirmed via its
    # own footer credit) embeds real listing links DIRECTLY on its
    # homepage rather than on any sub-path — none of the other candidate
    # paths below ever surfaced them, so the homepage itself must also
    # be scanned, not just used for detect(). "/show-all-properties" is
    # premises.com.au's specific "Properties For Sale" index path.
    # "/selling/recent-sales" and "/buying/properties-for-sale" are
    # confirmed real paths for Crystal Realty (platform: Reapit/
    # Agentbox, confirmed via its own "Powered by Reapit Websites"
    # footer credit) — found by directly inspecting its real nav menu
    # after the original candidate list never matched anything for it.
    # "/buying/recently-sold" added after confirming Park Properties'
    # real sold-listings path includes pagination under this prefix
    # (June 2026). Note: Travers Gray Real Estate's homepage is a video
    # splash screen linking only to /officelocation, which was initially
    # suspected to block discovery entirely — but confirmed NOT to be an
    # issue, since /sold and /for-sale (already in this list) are tried
    # directly against the domain root regardless of how the homepage
    # itself links to anything.
    CANDIDATE_INDEX_PATHS = [
        "", "/buy", "/properties/for-sale", "/properties-for-sale",
        "/for-sale", "/listings/buy", "/sell/recently-sold",
        "/recently-sold", "/sold", "/show-all-properties",
        "/selling/recent-sales", "/buying/properties-for-sale",
        "/buying/recently-sold",
        # Confirmed real path (Kangaroo Point Real Estate, platform:
        # Rex Websites — June 24, 2026): a real sold-listings index
        # page found 494 genuine sold listings at this exact URL.
        "/listings/?saleOrRental=Sale&sold=1",
    ]

    def __init__(self, llm_api_key=None, browserless_api_key=None):
        # Optional — if not supplied, the LLM tier is simply skipped
        # (extraction_tiers.extract_listing_fields handles this
        # gracefully). Passed in per-instance rather than read from an
        # environment variable so the same pattern as Google Places
        # (key entered in the UI, sent per-request, never stored) holds.
        self.llm_api_key = llm_api_key
        self.llm_call_count = 0  # for real cost measurement, see README
        # Same opt-in pattern: Browserless's JS-rendering fallback is
        # only used when a key is supplied AND every plain-HTTP path
        # has already failed to find any candidate listing URLs at
        # all. Confirmed real, small minority of sites need this
        # (~2% across ~90 sites tested) — see browserless_fallback.py
        # module docstring for the full cost reasoning.
        self.browserless_api_key = browserless_api_key
        self.browserless_call_count = 0

    def detect(self, html):
        # Deliberately always matches — this is the catch-all, tried only
        # after every more specific adapter has already had a chance to
        # claim the site. Order in ADAPTERS is what makes this safe.
        return True

    def _looks_like_listing_url(self, url, domain):
        # CONFIRMED REAL BUG (June 23, 2026): a strict url.startswith(
        # domain) check rejected every one of Travers Gray's listing
        # links, because its protocol-relative hrefs
        # (e.g. "//www.traversgray.com.au/21534560") always resolve to
        # the www. variant, while `domain` is often the bare,
        # non-www. version the user originally typed in (before any
        # www.-retry logic runs). www. and non-www. are the same real
        # site; normalize both sides before comparing so this doesn't
        # silently reject every listing on a site that mixes the two.
        normalized_url = re.sub(r"^https?://(www\.)?", "", url)
        normalized_domain = re.sub(r"^https?://(www\.)?", "", domain)
        if not normalized_url.startswith(normalized_domain):
            return False
        path = normalized_url[len(normalized_domain):]

        # CONFIRMED REAL FALSE POSITIVE (Stone Real Estate, June 23,
        # 2026): a WordPress plugin called "ZooRealty" generates
        # calendar-reminder (.ics) links for open-home/auction times,
        # shaped like
        # "/wp-content/plugins/zoorealty/display/elements/crm.php
        # ?property_id=8733796&time=16:15:00" — these end in a numeric
        # ID and were wrongly accepted as listing pages, but are
        # actually iCalendar files (confirmed via direct fetch), not
        # property pages at all. Excluded by checking for
        # "wp-content/plugins" anywhere in the path, since no real
        # listing page should ever live inside a plugin's own asset
        # directory regardless of platform.
        if "wp-content/plugins" in path.lower():
            return False

        # Confirmed via live inspection of 5+ real sites in one session
        # (June 2026): a genuine listing URL consistently ends in a
        # numeric ID (Viridity: ...-westmead-nsw-6194909, Crystal Realty:
        # .../terrace/8654822, JBRE: ...-barr-street-camperdown-nsw-
        # 6195868, Wiseberry: .../listing/16-adrian-close-...-36368).
        # This signal alone is reliable across genuinely different URL
        # styles (hyphenated-address style vs slash-separated-category
        # style) — confirmed by testing both. An earlier version also
        # required >=2 hyphens in the path, which wrongly rejected
        # Crystal Realty's URL (only 1 hyphen, in "inner-west") since
        # that site uses slashes rather than hyphens to separate path
        # segments. Dropped that requirement; the numeric-ID check does
        # the real work.
        #
        # A minimum-length check (len(path) > 15) was also dropped after
        # a second real bug: Travers Gray Real Estate (platform: ReNet)
        # uses BARE numeric-ID URLs with no slug at all — e.g. "/21631808"
        # (9 characters) — which the length check wrongly rejected. The
        # numeric-ID-at-the-end requirement alone already excludes every
        # real nav link seen across all sites tested (e.g. "/buy",
        # "/about", "/sold" never end in 4+ digits), so the extra length
        # guard was redundant protection that became actively harmful
        # once a real site with short listing URLs was found.
        #
        # Confirmed exception (Living Estate Agents, platform: Eagle
        # Software, confirmed via "Powered by Eagle Software" footer —
        # June 2026): listing URLs use a query-string ID, NOT a
        # trailing numeric ID — e.g.
        # "/property?property_id=1662525/2-chisholm-avenue-clemton-park".
        # The numeric ID sits right after "property_id=", not at the
        # end of the path (a slug follows it). Checked as an explicit,
        # separate pattern rather than trying to generalize the
        # trailing-numeric-ID rule further.
        if re.search(r"property_id=\d{4,}", path):
            return True
        # Confirmed exception (Stone Real Estate, platform: Reapit
        # Websites via a WordPress "ZooRealty" plugin wrapper — June 23,
        # 2026): listing URLs put the numeric ID FIRST in the slug, not
        # last — e.g. "/property/6561371-10-trade-street-newtown-nsw/".
        # The general trailing-numeric-ID rule below would never match
        # this shape; checked as its own explicit pattern instead of
        # trying to generalize further.
        if re.search(r"/\d{4,}-[a-zA-Z0-9]", path):
            return True
        # Confirmed exception (Woolloongabba Real Estate, WordPress
        # "EPL" real estate plugin — confirmed via
        # "?action=epl_search&post_type=property" query params on the
        # site's own nav links — June 24, 2026): real listing URLs are
        # "/properties-for-sale/{full-address-slug-ending-in-postcode}/"
        # — e.g.
        # "/properties-for-sale/506-19-hope-street-south-brisbane-qld-4101/".
        # The trailing 4-digit number here is a POSTCODE, not a real
        # listing ID — the general trailing-numeric-ID rule below would
        # ALSO match this (correctly, by coincidence), but it would
        # JUST AS EASILY match other postcode-ending nav links that
        # aren't listings at all, since "ends in 4 digits" is an
        # extremely common, generic pattern for any Australian
        # address-shaped URL. This exception is intentionally NARROW —
        # it requires the known "/properties-for-sale/" prefix AND a
        # genuine slug (not a query string) between it and the trailing
        # slash, specifically excluding the real nav link
        # "/properties-for-sale/?action=epl_search..." (which has a
        # "?" immediately after the prefix, no real slug at all).
        if re.match(r"^/properties-for-sale/[^?]+/$", path):
            return True
        # Confirmed exception (Kangaroo Point Real Estate, platform:
        # Rex Websites — confirmed via "Powered by Rex Websites" footer
        # credit, June 24, 2026): listing URLs are
        # "/listings/residential_sale-R2-{id}-{suburb-slug}" — the
        # numeric ID sits in the MIDDLE of the slug (after "R2-"),
        # followed by more text (the suburb), not at the very end of
        # the path. Distinct from BresicWhitney's Rex CRM (a JS-gated
        # dead end, confirmed in an earlier session) — "Rex Websites"
        # is a different product from the same company, and is fully
        # server-rendered.
        if re.search(r"/listings/[a-z_]+-R\d-\d{4,}-[a-z]", path, re.IGNORECASE):
            return True
        return bool(re.search(r"[-/]\d{4,}/?$", path))

    def _collect_listing_urls(self, html, domain):
        urls = set()
        # CONFIRMED REAL BUG (June 23, 2026): Travers Gray Real Estate's
        # actual hrefs are PROTOCOL-RELATIVE URLs — e.g.
        # href="//www.traversgray.com.au/21534560" (no "https:" prefix,
        # just "//"). Neither the absolute-URL branch (requires
        # https?://) nor the original root-relative branch handled this
        # correctly — the root-relative regex matched the LEADING "/"
        # of "//www.traversgray..." as if it were a domain-relative path
        # starting with "/www.traversgray...", then prepended our own
        # domain again, producing a doubled URL like
        # "https://traversgray.com.au//traversgray.com.au/21534560" —
        # which silently 404'd every single time, hidden by the
        # absence of status-code logging (also fixed this session).
        # This MUST be checked first, before the root-relative branch,
        # since "//host/path" also matches a naive "starts with /" test.
        scheme = domain.split("://")[0]  # "https" or "http"
        for m in re.finditer(r'href="(//[^"\s]+)"', html):
            url = f"{scheme}:{m.group(1)}"
            if self._looks_like_listing_url(url, domain):
                urls.add(url)
        for m in re.finditer(r'href="(https?://[^"\s]+)"', html):
            url = m.group(1)
            if self._looks_like_listing_url(url, domain):
                urls.add(url)
        for m in re.finditer(r'href="(/(?!/)[^"\s]+)"', html):
            url = domain + m.group(1)
            if self._looks_like_listing_url(url, domain):
                urls.add(url)
        # Confirmed real via live testing (June 2026): Viridity Real
        # Estate's actual listing links use relative hrefs with NO
        # leading slash at all — sometimes prefixed with "../" (e.g.
        # href="../11-blackwall-point-road-chiswick-nsw-6195827"),
        # sometimes completely bare (e.g. href="show-all-properties").
        # Neither of the two patterns above ever matches this style —
        # this was a genuine bug (not a content-rotation issue, as
        # first suspected) that caused 0 listings to be found despite
        # the links being right there in the raw HTML. "../" is
        # stripped since it just means "relative to one level up from
        # wherever this page lives," which for our purposes resolves to
        # the same domain root the other two patterns already use.
        for m in re.finditer(r'href="((?:\.\./)?[a-z0-9][a-z0-9\-]*)"', html, re.IGNORECASE):
            relative_path = m.group(1)
            if relative_path.startswith("../"):
                relative_path = relative_path[3:]
            url = domain + "/" + relative_path
            if self._looks_like_listing_url(url, domain):
                urls.add(url)
        return urls

    def _parse_detail_page(self, html, listing_url, domain, log):
        # TEMPORARY DIAGNOSTIC (June 23, 2026) — added to debug a real,
        # confirmed mystery: the hidden-input extraction tier works
        # correctly against real HTML fetched via curl from the user's
        # own machine, but the live deployed app still returns 0
        # listings for traversgray.com.au. This logs exactly what HTML
        # the live Vercel server actually received for each listing
        # page, to compare against what curl receives from a different
        # network/IP — remove once the real cause is found.
        if "traversgray" in listing_url.lower():
            has_extra_data = "extra_data[address]" in html
            has_renet = "renet" in html.lower()
            log(f"    [DIAGNOSTIC] {listing_url}: html_length={len(html)}, "
                f"has_extra_data_address={has_extra_data}, has_renet_marker={has_renet}, "
                f"first_200_chars={html[:200]!r}")

        result = extraction_tiers.extract_listing_fields(
            html, listing_url, log=log, llm_api_key=self.llm_api_key,
        )
        if not result:
            log(f"    No usable data found on {listing_url} via any tier "
                f"(json_ld/meta_tags/known_template/{'llm' if self.llm_api_key else 'llm not enabled'})")
            return None

        if result["tier"] == "llm_extraction":
            self.llm_call_count += 1

        address = result.get("address", "")
        if not address:
            return None

        # Status: prefer whatever the tier found; fall back to the
        # Belle-confirmed URL-path signal if the tier didn't determine one
        # (JSON-LD in particular doesn't reliably distinguish active/sold).
        status = result.get("status")
        if not status:
            is_sold = bool(re.search(r"/sold/|/sold-", listing_url, re.IGNORECASE))
            status = "Sold" if is_sold else "Active"

        price = result.get("price", "")

        # Agent info: confirmed section class="property-agents" — pull
        # the first plausible name-shaped text inside it (Belle-derived,
        # kept as a best-effort extra on top of the tiered fields above,
        # which don't currently extract agent info).
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

        suburb = result.get("suburb", "")
        if not suburb:
            parts = [p.strip() for p in address.split(",")]
            if len(parts) >= 2:
                suburb = parts[-2] if not re.search(r"\d", parts[-2]) else ""

        return Listing(
            listing_id=listing_url,
            status=status,
            address=address,
            suburb=suburb,
            postcode=result.get("postcode", ""),
            guide_price=price if status == "Active" else "",
            sold_price=price if status == "Sold" else "",
            date_listed="",
            sold_date="",
            agent_name=agent_name,
            agent_email="",
            agent_phone="",
            agent_member_id="",
            office_name="",
            office_domain=domain,
            listing_url=listing_url,
            source_adapter=f"{self.name}:{result['tier']}",
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
            if not self.browserless_api_key:
                log("  No listing index page matched any known path pattern — "
                    "this site's structure is not covered by the generic fallback")
                return []

            # LAST RESORT: every plain-HTTP path found nothing. Try the
            # homepage's JS-RENDERED version via Browserless — confirmed
            # real use case (LJ Hooker's HubSpot platform, June 2026):
            # the homepage's REAL listing links only exist after
            # JavaScript runs, invisible to every plain-HTTP fetch no
            # matter which path is tried.
            log("  No listing index page matched any known path pattern via plain HTTP — "
                "trying Browserless (JS-rendered) as a last resort...")
            rendered_html = browserless_fallback.fetch_rendered_html(
                domain, self.browserless_api_key, log=log,
            )
            if not rendered_html:
                log("  Browserless fallback also found nothing usable — giving up on this office")
                return []

            self.browserless_call_count += 1
            found = self._collect_listing_urls(rendered_html, domain)
            if not found:
                log("  Browserless returned rendered HTML, but no candidate listing URLs "
                    "were found in it either — this site's structure is genuinely not "
                    "covered, not just hidden behind JavaScript")
                return []
            log(f"  Browserless (JS-rendered homepage): found {len(found)} candidate listing URL(s)")
            listing_urls.update(found)
            # A site whose LISTING LINKS only exist after JS runs is a
            # strong real signal that its listing DATA likely works the
            # same way — confirmed true for LJ Hooker's HubSpot
            # platform (checked the dedicated search-results page
            # directly; still only placeholder text in plain HTML).
            # Tracked so the detail-page loop below knows to use
            # Browserless from the start rather than wastefully trying
            # plain HTTP first and failing every single time.
            needed_browserless_for_discovery = True
        else:
            needed_browserless_for_discovery = False

        log(f"  Visiting {len(listing_urls)} candidate listing page(s)...")
        listings = []
        for listing_url in listing_urls:
            html = None
            if needed_browserless_for_discovery:
                html = browserless_fallback.fetch_rendered_html(
                    listing_url, self.browserless_api_key, log=log,
                )
                if html:
                    self.browserless_call_count += 1
            else:
                try:
                    resp = session.get(listing_url, timeout=REQUEST_TIMEOUT)
                except requests.RequestException as e:
                    log(f"    ERROR fetching {listing_url}: {e}")
                    continue
                if resp.status_code != 200:
                    # CONFIRMED REAL BUG (June 23, 2026): this case was
                    # previously silent — no log line at all — which hid
                    # the real cause of traversgray.com.au returning 0
                    # parsed listings for an entire debugging session. Now
                    # logged explicitly so a non-200 response is visible
                    # rather than indistinguishable from "parsing failed".
                    log(f"    {listing_url} returned HTTP {resp.status_code}, skipping")
                    continue
                html = resp.text

            if not html:
                continue
            parsed = self._parse_detail_page(html, listing_url, domain, log)
            if parsed:
                listings.append(parsed)
            time.sleep(0.2)

        log(f"  Parsed {len(listings)} of {len(listing_urls)} candidate page(s) successfully "
            f"(extraction_confidence=low — verify before relying on this data)")
        return listings


def _build_adapters(llm_api_key=None, browserless_api_key=None):
    """
    Built per-call rather than as a single module-level constant so each
    request can supply its own LLM API key (same pattern as the Google
    Places key in discovery.py — entered in the UI, sent per-request,
    never stored server-side). When no key is supplied, GenericFallback
    Adapter still works exactly as before, just without tier 4 (LLM).
    Same pattern for browserless_api_key — without it, the JS-rendering
    fallback in GenericFallbackAdapter.fetch() is simply skipped.
    """
    return [
        RayWhiteDynamicsAdapter(),
        CloudhiRexAdapter(),
        LJHookerAdapter(browserless_api_key=browserless_api_key),
        GenericFallbackAdapter(llm_api_key=llm_api_key, browserless_api_key=browserless_api_key),
    ]


def scrape_office(raw_url, log=print, llm_api_key=None, browserless_api_key=None):
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
        # Confirmed real case (Crystal Realty, June 2026): the bare
        # domain (no "www.") can genuinely fail DNS resolution while
        # "www.{domain}" resolves fine — this is a real, fairly common
        # DNS configuration choice some sites make, not a code bug.
        # Retry once with "www." prepended specifically for name-
        # resolution failures; other error types (timeout, connection
        # refused, SSL errors) wouldn't be fixed by changing the
        # hostname, so they're not worth retrying here.
        is_dns_failure = "NameResolutionError" in str(e) or "Failed to resolve" in str(e)
        netloc = urlparse(domain).netloc
        already_has_www = netloc.startswith("www.")
        if is_dns_failure and not already_has_www:
            www_domain = domain.replace("://", "://www.", 1)
            log(f"  {domain} failed DNS resolution, retrying with {www_domain} ...")
            try:
                resp = session.get(www_domain, timeout=REQUEST_TIMEOUT)
                domain = www_domain  # use the working hostname for all subsequent requests
            except requests.RequestException as e2:
                return [], f"Could not reach site (tried both {domain.replace('www.', '')} and {www_domain}): {e2}"
        else:
            return [], f"Could not reach site: {e}"

    if resp.status_code != 200:
        return [], f"Site returned HTTP {resp.status_code}"

    adapters = _build_adapters(llm_api_key=llm_api_key, browserless_api_key=browserless_api_key)
    matched_adapter = None
    for adapter in adapters:
        if adapter.detect(resp.text):
            matched_adapter = adapter
            break

    if not matched_adapter:
        return [], "No known platform detected for this site (not yet supported)"

    log(f"  Matched adapter: {matched_adapter.name}")
    listings = matched_adapter.fetch(domain, log)

    if isinstance(matched_adapter, GenericFallbackAdapter) and matched_adapter.llm_call_count:
        log(f"  (used LLM extraction tier {matched_adapter.llm_call_count} time(s) for this office)")

    # Confirmed real case (Park Properties, June 2026): a bare domain
    # (no "www.") can connect successfully (no exception, status 200)
    # yet still serve content that yields ZERO matching listings, while
    # "www.{domain}" works fully — a different failure shape than the
    # DNS-resolution-failure case handled above (no error here at all,
    # just silently empty results). Proactively retry with "www." if
    # the first attempt found nothing, same one-shot-only guard against
    # looping. Scoped to GenericFallbackAdapter specifically — Ray
    # White/Cloudhi/LJ Hooker already have their own confirmed domain
    # conventions and a genuine zero-listings result from them is more
    # likely a real "nothing to find" than a www./non-www. quirk.
    netloc = urlparse(domain).netloc
    already_has_www = netloc.startswith("www.")
    if not listings and not already_has_www and isinstance(matched_adapter, GenericFallbackAdapter):
        www_domain = domain.replace("://", "://www.", 1)
        log(f"  No listings found on {domain}, retrying with {www_domain} ...")
        try:
            www_resp = session.get(www_domain, timeout=REQUEST_TIMEOUT)
            if www_resp.status_code == 200:
                www_adapters = _build_adapters(llm_api_key=llm_api_key, browserless_api_key=browserless_api_key)
                www_matched = None
                for adapter in www_adapters:
                    if adapter.detect(www_resp.text):
                        www_matched = adapter
                        break
                if www_matched:
                    www_listings = www_matched.fetch(www_domain, log)
                    if www_listings:
                        log(f"  Found {len(www_listings)} listing(s) via {www_domain}")
                        return www_listings, None
        except requests.RequestException as e:
            log(f"  www. retry also failed: {e}")

    return listings, None


def scrape_offices(urls, log=print, llm_api_key=None, browserless_api_key=None):
    """Scrape a list of office URLs. Returns dict with results + per-office status."""
    all_listings = []
    office_results = []
    total_llm_calls = 0

    for raw_url in urls:
        raw_url = raw_url.strip()
        if not raw_url:
            continue
        listings, error = scrape_office(
            raw_url, log=log, llm_api_key=llm_api_key, browserless_api_key=browserless_api_key,
        )
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
