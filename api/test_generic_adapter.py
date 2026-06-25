"""Tests for GenericFallbackAdapter — the last-resort, low-confidence
catch-all adapter, built from live inspection of Belle Property (June 2026)
but designed to be tried broadly across unrecognized agency sites."""
import sys
sys.path.insert(0, ".")
import scraper as scraper_module
from scraper import GenericFallbackAdapter, _build_adapters, RayWhiteDynamicsAdapter, CloudhiRexAdapter


FAKE_ACTIVE_DETAIL = """
<html><body>
<div class="col column-main">
<h1 class="address">12 Example Street, Mermaid Waters QLD 4218</h1>
<div class="features">beds baths car</div>
<div class="price">Offers from $795,000</div>
<section class="section property-agents">
  <div class="agent"><span>John Smith</span></div>
</section>
</div>
</body></html>
"""

FAKE_SOLD_DETAIL = """
<html><body>
<div class="col column-main">
<h1 class="address">45 Sample Road, Burleigh Heads QLD 4220</h1>
<div class="price">$1,335,000</div>
<section class="section property-agents">
  <div class="agent"><span>Jane Doe</span></div>
</section>
</div>
</body></html>
"""


def test_adapter_order_generic_is_last():
    """The generic adapter's detect() always returns True, so it MUST be
    registered after Ray White and Cloudhi or it would silently steal
    every site, including ones the precise adapters should handle."""
    adapters = _build_adapters()
    assert adapters[-1].name == "generic_fallback"
    assert any(isinstance(a, RayWhiteDynamicsAdapter) for a in adapters[:-1])
    assert any(isinstance(a, CloudhiRexAdapter) for a in adapters[:-1])
    print("PASS: generic_fallback is registered last, after both precise adapters")


def test_detect_always_true():
    adapter = GenericFallbackAdapter()
    assert adapter.detect("<html>anything at all</html>") is True
    assert adapter.detect("") is True
    print("PASS: detect() always matches (by design, as the last-resort catch-all)")


def test_active_listing_parsed_via_confirmed_classes():
    adapter = GenericFallbackAdapter()
    logs = []
    result = adapter._parse_detail_page(
        FAKE_ACTIVE_DETAIL, "https://belleproperty.com/12-example-street-mermaid-waters-qld-4218",
        "https://belleproperty.com", logs.append
    )
    assert result.status == "Active"
    assert result.address == "12 Example Street, Mermaid Waters QLD 4218"
    assert result.guide_price == "795000"
    assert result.sold_price == ""
    assert result.agent_name == "John Smith"
    assert result.extraction_confidence == "low", "Generic adapter must always report low confidence"
    print("PASS: active listing parsed via confirmed class=\"price\"/class=\"address\"")


def test_sold_status_determined_by_url_not_page_text():
    """Confirmed approach: sold status comes from the URL path (e.g. a
    /sold/ segment), NOT from any text on the page itself — the sold
    fixture here contains no 'sold' text anywhere, only a /sold/ URL."""
    adapter = GenericFallbackAdapter()
    logs = []
    result = adapter._parse_detail_page(
        FAKE_SOLD_DETAIL, "https://belleproperty.com/sold/45-sample-road-burleigh-heads-qld-4220",
        "https://belleproperty.com", logs.append
    )
    assert result.status == "Sold", f"FAIL: {result.status}"
    assert result.sold_price == "1335000"
    assert result.guide_price == ""
    assert "sold" not in FAKE_SOLD_DETAIL.lower(), "Fixture must not contain the word 'sold' as page text, to prove this is URL-based detection"
    print("PASS: sold status correctly determined from URL path alone, not page text")


def test_active_url_without_sold_segment_stays_active():
    adapter = GenericFallbackAdapter()
    logs = []
    result = adapter._parse_detail_page(
        FAKE_ACTIVE_DETAIL, "https://belleproperty.com/12-example-street-mermaid-waters-qld-4218",
        "https://belleproperty.com", logs.append
    )
    assert result.status == "Active"
    print("PASS: a URL without a /sold/ segment correctly stays Active")


def test_missing_address_returns_none_not_garbage():
    adapter = GenericFallbackAdapter()
    html_no_address = "<html><body><div class=\"price\">$500,000</div></body></html>"
    logs = []
    result = adapter._parse_detail_page(html_no_address, "https://example.com/some-page", "https://example.com", logs.append)
    assert result is None, "Should refuse to guess without an address, not return a half-populated row"
    print("PASS: missing address correctly returns None rather than guessing")


def test_looks_like_listing_url_heuristic():
    adapter = GenericFallbackAdapter()
    domain = "https://belleproperty.com"
    assert adapter._looks_like_listing_url(
        "https://belleproperty.com/12-example-street-mermaid-waters-qld-4218", domain
    ) is True
    assert adapter._looks_like_listing_url("https://belleproperty.com/buy", domain) is False, (
        "Short nav-like paths should not be mistaken for listing URLs"
    )
    assert adapter._looks_like_listing_url("https://otherdomain.com/some-listing-page", domain) is False, (
        "URLs on a different domain should never be treated as this office's listings"
    )
    print("PASS: listing-URL heuristic distinguishes real listings from nav links and other domains")


def test_collect_listing_urls_finds_homepage_embedded_listings():
    """
    Regression test for a real bug found via live testing (June 2026):
    Viridity Real Estate (platform: premises.com.au) embeds real listing
    links DIRECTLY on its homepage, not on any sub-path. The candidate
    index path list previously never included the bare homepage itself
    ("" was missing), so these real listings were never found even
    though they were sitting right there in the page that gets fetched
    anyway for detect(). Confirmed fix: "" added to CANDIDATE_INDEX_PATHS.
    """
    adapter = GenericFallbackAdapter()
    domain = "https://viridityre.com.au"

    real_homepage_snippet = """
    <html><body>
    <a href="https://viridityre.com.au/buying">Buying</a>
    <a href="https://viridityre.com.au/show-all-properties">Properties For Sale</a>
    <a href="https://viridityre.com.au/upcoming-inspections-for-sale">Open Homes</a>
    <a href="https://viridityre.com.au/68-74-church-street-cranebrook-nsw-6195951">
      <img src="...">
    </a>
    <a href="https://viridityre.com.au/11-blackwall-point-road-chiswick-nsw-6195827">
      <img src="...">
    </a>
    </body></html>
    """

    found = adapter._collect_listing_urls(real_homepage_snippet, domain)
    assert len(found) == 2, f"Expected 2 real listings, got {len(found)}: {found}"
    assert any("6195951" in url for url in found)
    assert any("6195827" in url for url in found)
    assert not any("show-all-properties" in url for url in found), "Nav link should not be mistaken for a listing"
    assert "" in adapter.CANDIDATE_INDEX_PATHS, (
        "The bare homepage path must be in CANDIDATE_INDEX_PATHS for fetch() to ever scan it"
    )
    print("PASS: homepage-embedded listings are found, and the bare homepage path is "
          "confirmed present in CANDIDATE_INDEX_PATHS (the actual fix)")


def test_collect_listing_urls_handles_relative_hrefs():
    """
    Regression test for a real bug found via live testing (June 2026):
    Viridity Real Estate's actual listing links use relative hrefs with
    NO leading slash — sometimes "../address-nsw-123456", sometimes a
    completely bare "show-all-properties" with no prefix at all. The
    original _collect_listing_urls() only matched absolute
    (https://domain/...) and root-relative (/path) hrefs — neither
    pattern ever matches this style, which meant 0 listings were found
    even though they were sitting right there in the raw HTML, fetched
    directly via curl with no rendering involved. Fixture below is
    built directly from real curl output against the live site.
    """
    adapter = GenericFallbackAdapter()
    domain = "https://viridityre.com.au"

    real_href_dump = """
    <html><body>
    <a href="../"></a>
    <a href="../11-blackwall-point-road-chiswick-nsw-6195827"></a>
    <a href="../15-100-william-street-five-dock-nsw-6196139"></a>
    <a href="../about"></a>
    <a href="../contact"></a>
    <a href="../search-rentals"></a>
    <a href="/"></a>
    <a href="/about-us"></a>
    <a href="/upcoming-inspections-for-sale"></a>
    <a href="show-all-properties"></a>
    <a href="land-for-sale"></a>
    <a href="recent-sales"></a>
    </body></html>
    """

    found = adapter._collect_listing_urls(real_href_dump, domain)
    assert any("6195827" in url for url in found), "FAIL: should find ../-prefixed relative listing link"
    assert any("6196139" in url for url in found), "FAIL: should find second ../-prefixed relative listing link"
    assert not any(url.rstrip("/").endswith("show-all-properties") for url in found), (
        "FAIL: bare relative nav link should not be mistaken for a listing"
    )
    assert not any(url.rstrip("/").endswith("recent-sales") for url in found), (
        "FAIL: bare relative nav link should not be mistaken for a listing"
    )
    print("PASS: relative href styles (../path and bare path) are correctly handled, "
          "real listings found and nav links correctly excluded")


def test_wordpress_plugin_calendar_urls_excluded():
    """
    Regression test for a real false positive found via live testing
    (Stone Real Estate, June 23, 2026): a WordPress plugin called
    "ZooRealty" generates calendar-reminder (.ics) links for open-home/
    auction times, shaped like
    "/wp-content/plugins/zoorealty/display/elements/crm.php
    ?property_id=8733796&time=16:15:00" — confirmed via direct fetch to
    be an iCalendar file, NOT a property listing page, despite ending
    in a numeric ID that satisfied the existing heuristic.
    """
    adapter = GenericFallbackAdapter()
    domain = "https://stonerealestate.com.au"
    calendar_url = (
        "https://www.stonerealestate.com.au/wp-content/plugins/zoorealty/"
        "display/elements/crm.php?property_id=8733796&time=16:15:00"
    )
    assert not adapter._looks_like_listing_url(calendar_url, domain), (
        "FAIL: should reject WordPress plugin calendar widget URLs"
    )
    print("PASS: WordPress plugin calendar-widget URLs are correctly excluded")


def test_id_first_slug_url_pattern():
    """
    Confirmed real exception (Stone Real Estate, platform: Reapit
    Websites via a WordPress "ZooRealty" wrapper — June 23, 2026):
    listing URLs put the numeric ID FIRST in the slug, not last — e.g.
    "/property/6561371-10-trade-street-newtown-nsw/". Also confirms a
    real bug found while building this exception: the first version of
    the regex required a LETTER immediately after the ID, but the real
    slug can have a numeric segment next (e.g. a street number like
    "10" right after the property ID), which a letter-only check missed.
    """
    adapter = GenericFallbackAdapter()
    domain = "https://stonerealestate.com.au"
    real_url = "https://www.stonerealestate.com.au/property/6561371-10-trade-street-newtown-nsw/"
    assert adapter._looks_like_listing_url(real_url, domain), (
        "FAIL: should accept the confirmed real ID-first listing URL pattern, "
        "including when a numeric street number immediately follows the ID"
    )
    print("PASS: ID-first slug URL pattern correctly accepted, including the "
          "numeric-segment-after-ID edge case")


def test_melita_bell_index_paths_in_candidate_list():
    """
    Confirmed real paths (The Melita Bell Team, RE/MAX Success
    franchise — June 24, 2026): a real sold-listings index page with
    471 pages of genuine results was found at /sold-residential.
    """
    adapter = GenericFallbackAdapter()
    assert "/sold-residential" in adapter.CANDIDATE_INDEX_PATHS
    assert "/current-residential-for-sale" in adapter.CANDIDATE_INDEX_PATHS
    print("PASS: Melita Bell / RE/MAX Success index paths present in the candidate list")


def test_rex_websites_index_path_in_candidate_list():
    """
    Confirmed real path (Kangaroo Point Real Estate, platform: Rex
    Websites — June 24, 2026): a real sold-listings index page found
    494 genuine sold listings at this exact URL. Must be present in
    CANDIDATE_INDEX_PATHS for fetch() to ever check it — confirmed via
    a real live test that, even after fixing the CloudhiRexAdapter
    false-positive routing bug, the site still returned 0 listings
    because this path simply wasn't in the candidate list at all.
    """
    adapter = GenericFallbackAdapter()
    assert "/listings/?saleOrRental=Sale&sold=1" in adapter.CANDIDATE_INDEX_PATHS, (
        "The confirmed real Rex Websites sold-listings path must be in CANDIDATE_INDEX_PATHS"
    )
    print("PASS: Rex Websites sold-listings index path is present in the candidate list")


def test_rex_websites_url_pattern_handles_multiple_real_id_formats():
    """
    Confirmed real generalization gap (Abra Agencies, June 25, 2026):
    the original Rex Websites URL exception (built from Kangaroo Point
    Real Estate) only matched the "R2-{numeric}" ID format. A SECOND
    real office on the same confirmed platform ("Powered by Rex
    Websites" footer credit) uses entirely different, letter-prefixed
    ID formats ("QTW27006", "L18768190") — Rex Websites apparently
    supports multiple ID schemes across different customer accounts.
    Broadened the pattern to handle both the original two-segment
    format and these new single-segment formats, without losing the
    original confirmed case.
    """
    adapter = GenericFallbackAdapter()

    real_urls = [
        ("https://abraagencies.com.au", "https://abraagencies.com.au/listings/residential_sale-QTW27006-harristown"),
        ("https://abraagencies.com.au", "https://abraagencies.com.au/listings/residential_sale-L18768190-rangeville"),
        ("https://abraagencies.com.au", "https://abraagencies.com.au/listings/land_sale-L18768007-glenvale"),
        ("https://abraagencies.com.au", "https://abraagencies.com.au/listings/commercial_rental-QTW26847-drayton"),
        ("https://kangaroopointrealestate.com.au", "https://kangaroopointrealestate.com.au/listings/residential_sale-R2-5091526-kangaroo-point"),
    ]
    for domain, url in real_urls:
        assert adapter._looks_like_listing_url(url, domain), f"FAIL: should accept {url}"

    # Real nav links on the same confirmed platform must still be rejected
    nav_urls = [
        ("https://abraagencies.com.au", "https://abraagencies.com.au/listings?saleOrRental=Sale&status=available_under_contract"),
        ("https://abraagencies.com.au", "https://abraagencies.com.au/listings"),
    ]
    for domain, url in nav_urls:
        assert not adapter._looks_like_listing_url(url, domain), f"FAIL: should reject {url}"

    print("PASS: Rex Websites URL pattern correctly handles multiple real confirmed ID formats "
          "across different customer accounts, still rejects real nav links")


def test_rex_websites_url_pattern():
    """
    Confirmed real exception (Kangaroo Point Real Estate, platform:
    Rex Websites — June 24, 2026): listing URLs are
    "/listings/residential_sale-R2-{id}-{suburb-slug}" — the numeric
    ID sits in the MIDDLE of the slug (after "R2-"), not at the end.
    Distinct from BresicWhitney's Rex CRM (a confirmed JS-gated dead
    end) — "Rex Websites" is a different, server-rendered product.
    """
    adapter = GenericFallbackAdapter()
    domain = "https://kangaroopointrealestate.com.au"
    real_url = "https://kangaroopointrealestate.com.au/listings/residential_sale-R2-5091526-kangaroo-point"
    assert adapter._looks_like_listing_url(real_url, domain), "FAIL: should accept the real Rex Websites listing URL"
    print("PASS: Rex Websites URL pattern correctly accepted")


def test_wordpress_epl_url_pattern_narrowly_scoped():
    """
    Confirmed real exception (Woolloongabba Real Estate, WordPress
    "EPL" plugin — June 24, 2026): real listing URLs are
    "/properties-for-sale/{full-address-slug-ending-in-postcode}/".
    The trailing 4-digit number is a POSTCODE, not a listing ID — a
    naive "ends in 4+ digits" rule would ALSO match real nav links
    like "/properties-for-sale/?action=epl_search..." in OTHER
    postcode-shaped contexts. This exception is intentionally narrow:
    it requires the specific "/properties-for-sale/" prefix AND a real
    slug (not a query string) — confirmed via testing that it accepts
    real listings while still rejecting the real nav link with a "?"
    immediately after the same prefix.
    """
    adapter = GenericFallbackAdapter()
    domain = "https://woolloongabbarealestate.com.au"

    real_listing = "https://woolloongabbarealestate.com.au/properties-for-sale/506-19-hope-street-south-brisbane-qld-4101/"
    assert adapter._looks_like_listing_url(real_listing, domain), "FAIL: should accept the real listing URL"

    nav_query_string = "https://woolloongabbarealestate.com.au/properties-for-sale/?action=epl_search&post_type=property&property_status=current"
    assert not adapter._looks_like_listing_url(nav_query_string, domain), (
        "FAIL: should reject the real nav link with a query string, same prefix as a real listing"
    )

    nav_other = "https://woolloongabbarealestate.com.au/open-homes/"
    assert not adapter._looks_like_listing_url(nav_other, domain)

    real_listing2 = "https://woolloongabbarealestate.com.au/properties-for-sale/306-18-hubert-street-woolloongabba-queensland-4102/"
    assert adapter._looks_like_listing_url(real_listing2, domain), "FAIL: should accept a second real listing URL"

    print("PASS: WordPress EPL URL pattern correctly accepts real listings, "
          "correctly rejects the real nav link sharing the same path prefix")


def test_eagle_software_property_id_url_pattern():
    """
    Confirmed real exception (Living Estate Agents, platform: Eagle
    Software — June 23, 2026): listing URLs use a query-string ID, not
    a trailing numeric ID — e.g.
    "/property?property_id=1662525/2-chisholm-avenue-clemton-park".
    """
    adapter = GenericFallbackAdapter()
    domain = "https://www.livingea.com.au"
    real_url = "https://www.livingea.com.au/property?property_id=1662525/2-chisholm-avenue-clemton-park"
    assert adapter._looks_like_listing_url(real_url, domain), (
        "FAIL: should accept the confirmed real Eagle Software property_id URL pattern"
    )
    print("PASS: Eagle Software's property_id query-string URL pattern is correctly accepted")


def test_protocol_relative_urls_resolved_without_doubling():
    """
    Regression test for THE real bug behind a multi-hour debugging
    session on traversgray.com.au (June 23, 2026). Confirmed via direct
    curl that Travers Gray's real listing hrefs are PROTOCOL-RELATIVE
    URLs: href="//www.traversgray.com.au/21534560" (no "https:" prefix).
    Two compounding bugs were found:
      1. Neither the absolute-URL nor original root-relative regex
         handled "//host/path" correctly — the root-relative branch
         matched the leading "/" and re-prepended our own domain,
         producing a doubled URL like
         "https://traversgray.com.au//traversgray.com.au/21534560",
         which silently 404'd on every single listing, every time,
         hidden by the absence of status-code logging (also fixed this
         session in scraper.py's fetch() loop).
      2. Even after adding explicit protocol-relative handling, the
         resolved URL (www.traversgray.com.au) didn't match the
         original bare domain (traversgray.com.au) the user typed in,
         because _looks_like_listing_url() did a strict prefix check
         with no www. normalization — so EVERY found URL was then
         rejected by the heuristic, a second bug masking the first.
    """
    adapter = GenericFallbackAdapter()
    domain = "https://traversgray.com.au"

    html = '<a href="//www.traversgray.com.au/21534560">listing</a>'
    found = adapter._collect_listing_urls(html, domain)
    assert len(found) == 1, f"FAIL: expected 1 URL from protocol-relative href, got {found}"
    url = list(found)[0]
    assert url == "https://www.traversgray.com.au/21534560", (
        f"FAIL: got {url!r} — domain doubling or wrong scheme"
    )
    assert "//traversgray.com.au//" not in url, "FAIL: domain doubling bug has regressed"
    print("PASS: protocol-relative href resolved correctly with no domain doubling")

    # The www./non-www. normalization specifically
    assert adapter._looks_like_listing_url(
        "https://www.traversgray.com.au/21631808", "https://traversgray.com.au"
    ), "FAIL: www. variant of a URL should match a non-www. domain"
    assert adapter._looks_like_listing_url(
        "https://traversgray.com.au/21631808", "https://www.traversgray.com.au"
    ), "FAIL: non-www. variant of a URL should match a www. domain"
    print("PASS: www./non-www. variants of the same domain correctly match each other")


def test_other_real_url_styles_unaffected_by_protocol_relative_fix():
    """Confirms the protocol-relative fix didn't regress any other
    confirmed real URL style from earlier in this project."""
    adapter = GenericFallbackAdapter()

    cases = [
        ("https://viridityre.com.au/76-3-reid-avenue-westmead-nsw-6194909", "https://viridityre.com.au"),
        ("https://www.crystalrealty.com.au/sale/nsw/inner-west/newtown/residential/terrace/8654822", "https://www.crystalrealty.com.au"),
        ("https://www.jbreproperty.com.au/803-30-barr-street-camperdown-nsw-6195868", "https://www.jbreproperty.com.au"),
        ("https://www.wiseberry.com.au/listing/16-adrian-close-bateau-bay-nsw-2261-36368", "https://www.wiseberry.com.au"),
        ("https://www.parkproperties.com.au/sale/nsw/inner-west/erskineville/residential/apartment/8661195", "https://www.parkproperties.com.au"),
    ]
    for url, dom in cases:
        assert adapter._looks_like_listing_url(url, dom), f"FAIL: regression on {url}"

    html_relative = '<a href="../11-blackwall-point-road-chiswick-nsw-6195827"></a>'
    found = adapter._collect_listing_urls(html_relative, "https://viridityre.com.au")
    assert "https://viridityre.com.au/11-blackwall-point-road-chiswick-nsw-6195827" in found

    print("PASS: all previously confirmed real URL styles still work after the protocol-relative fix")


def test_listing_url_heuristic_accepts_bare_numeric_id_urls():
    """
    Regression test for a real bug found via live testing (June 2026):
    Travers Gray Real Estate (platform: ReNet) uses BARE numeric-ID
    listing URLs with no slug or hyphens at all — e.g. "/21631808" (9
    characters). The original heuristic required len(path) > 15, which
    wrongly rejected these short, genuinely valid listing URLs. The
    numeric-ID-at-the-end requirement alone already excludes every real
    nav link confirmed across every site tested (nav links never end in
    4+ digits), so the length check was redundant and actively harmful.
    """
    adapter = GenericFallbackAdapter()
    domain = "https://www.traversgray.com.au"

    real_listing_urls = [
        "https://www.traversgray.com.au/21631808",
        "https://www.traversgray.com.au/21621319",
    ]
    for url in real_listing_urls:
        assert adapter._looks_like_listing_url(url, domain), f"FAIL: should accept bare numeric ID URL {url}"

    nav_urls = [
        "https://www.traversgray.com.au/for-sale",
        "https://www.traversgray.com.au/sold",
        "https://www.traversgray.com.au/about",
        "https://www.traversgray.com.au/",
    ]
    for url in nav_urls:
        assert not adapter._looks_like_listing_url(url, domain), f"FAIL: should reject nav link {url}"

    print("PASS: bare numeric-ID listing URLs (no slug, no hyphens) are correctly accepted, "
          "nav links still correctly rejected")


def test_listing_url_heuristic_real_world_confirmed_urls():
    """
    Regression test for a real bug found via live testing (June 2026):
    the original heuristic (hyphen count >= 2 AND length > 20) wrongly
    ACCEPTED nav/category pages like "/upcoming-inspections-for-sale"
    and "/recently-sold-page-2" on viridityre.com.au, while the real
    listing URL never even made it into the candidate set because nav
    pages crowded it out. Fixed to require a trailing numeric ID
    instead, confirmed to work across multiple genuinely different real
    URL styles (hyphenated-address style AND slash-separated-category
    style, which an earlier fix attempt incorrectly rejected for
    Crystal Realty specifically).
    """
    adapter = GenericFallbackAdapter()

    real_urls = [
        ("https://viridityre.com.au/76-3-reid-avenue-westmead-nsw-6194909", "https://viridityre.com.au"),
        ("https://www.crystalrealty.com.au/sale/nsw/inner-west/newtown/residential/terrace/8654822", "https://www.crystalrealty.com.au"),
        ("https://www.jbreproperty.com.au/803-30-barr-street-camperdown-nsw-6195868", "https://www.jbreproperty.com.au"),
        ("https://www.wiseberry.com.au/listing/16-adrian-close-bateau-bay-nsw-2261-36368", "https://www.wiseberry.com.au"),
        ("https://www.parkproperties.com.au/sale/nsw/inner-west/erskineville/residential/apartment/8661195", "https://www.parkproperties.com.au"),
    ]
    for url, dom in real_urls:
        assert adapter._looks_like_listing_url(url, dom), f"FAIL: should accept real listing URL {url}"

    fake_nav_pages = [
        ("https://viridityre.com.au/upcoming-inspections-for-sale", "https://viridityre.com.au"),
        ("https://viridityre.com.au/recently-sold-page-2", "https://viridityre.com.au"),
        ("https://viridityre.com.au/buy", "https://viridityre.com.au"),
        ("https://viridityre.com.au/about-us", "https://viridityre.com.au"),
    ]
    for url, dom in fake_nav_pages:
        assert not adapter._looks_like_listing_url(url, dom), f"FAIL: should reject nav/category page {url}"

    print("PASS: heuristic correctly accepts 5 real confirmed listing URLs across 2 different "
          "URL styles, and rejects 4 real nav/category pages that previously caused a live bug")


def test_browserless_fallback_triggers_only_when_plain_http_finds_nothing():
    """
    Confirmed real use case (LJ Hooker's HubSpot platform generation,
    June 24, 2026): the homepage's real listing links only exist after
    JavaScript runs — invisible to every plain-HTTP candidate path no
    matter which one is tried. When a browserless_api_key is supplied
    AND every plain-HTTP path finds zero candidates, the fallback
    fetches the JS-rendered homepage via Browserless and tries
    discovery again on that rendered HTML.
    """
    from unittest.mock import patch

    class FakeResponse:
        def __init__(self, text, status_code=200):
            self.text = text
            self.status_code = status_code

    class FakeSessionAllEmpty:
        def __init__(self):
            self.headers = {}
        def get(self, url, timeout=None):
            return FakeResponse("<html><body>no real links here</body></html>")

    rendered_html = """
    <html><body>
    <a href="https://example.com/123-test-street-suburb-456789">Listing 1</a>
    </body></html>
    """

    original_session = scraper_module.requests.Session
    scraper_module.requests.Session = FakeSessionAllEmpty
    try:
        adapter = GenericFallbackAdapter(browserless_api_key="fake-token")
        with patch.object(scraper_module.browserless_fallback, "fetch_rendered_html", return_value=rendered_html):
            logs = []
            listings = adapter.fetch("https://example.com", log=logs.append)
            assert any("trying Browserless" in l for l in logs)
            assert adapter.browserless_call_count >= 1
    finally:
        scraper_module.requests.Session = original_session
    print("PASS: Browserless fallback correctly triggers only when plain HTTP finds zero candidates")


def test_browserless_not_triggered_for_normal_working_sites():
    """The 98% case: a site plain HTTP already handles correctly must
    be completely unaffected by this feature, even when a
    browserless_api_key IS supplied — Browserless should never even
    be considered if plain HTTP already found candidates."""
    class FakeResponse:
        def __init__(self, text, status_code=200):
            self.text = text
            self.status_code = status_code

    real_homepage = '<a href="https://example.com/123-test-street-suburb-456789">Listing 1</a>'
    real_detail = '<h1 class="address">123 Test Street, Suburb</h1><div class="price">$750,000</div>'

    class FakeSessionNormalSite:
        def __init__(self):
            self.headers = {}
        def get(self, url, timeout=None):
            if url == "https://example.com":
                return FakeResponse(real_homepage)
            return FakeResponse(real_detail)

    original_session = scraper_module.requests.Session
    scraper_module.requests.Session = FakeSessionNormalSite
    try:
        # Note: browserless_api_key IS supplied here, to specifically
        # confirm it's the zero-candidates CONDITION that matters, not
        # merely the absence of a key
        adapter = GenericFallbackAdapter(browserless_api_key="fake-token")
        logs = []
        listings = adapter.fetch("https://example.com", log=logs.append)
        assert len(listings) == 1
        assert not any("Browserless" in l for l in logs), (
            "Browserless should never be mentioned for a site plain HTTP already handles"
        )
        assert adapter.browserless_call_count == 0
    finally:
        scraper_module.requests.Session = original_session
    print("PASS: a normal working site is completely unaffected, even with a Browserless key supplied")


if __name__ == "__main__":
    test_adapter_order_generic_is_last()
    test_detect_always_true()
    test_active_listing_parsed_via_confirmed_classes()
    test_sold_status_determined_by_url_not_page_text()
    test_active_url_without_sold_segment_stays_active()
    test_missing_address_returns_none_not_garbage()
    test_looks_like_listing_url_heuristic()
    test_collect_listing_urls_finds_homepage_embedded_listings()
    test_collect_listing_urls_handles_relative_hrefs()
    test_wordpress_plugin_calendar_urls_excluded()
    test_id_first_slug_url_pattern()
    test_melita_bell_index_paths_in_candidate_list()
    test_rex_websites_index_path_in_candidate_list()
    test_rex_websites_url_pattern()
    test_rex_websites_url_pattern_handles_multiple_real_id_formats()
    test_wordpress_epl_url_pattern_narrowly_scoped()
    test_eagle_software_property_id_url_pattern()
    test_protocol_relative_urls_resolved_without_doubling()
    test_other_real_url_styles_unaffected_by_protocol_relative_fix()
    test_listing_url_heuristic_accepts_bare_numeric_id_urls()
    test_listing_url_heuristic_real_world_confirmed_urls()
    test_browserless_fallback_triggers_only_when_plain_http_finds_nothing()
    test_browserless_not_triggered_for_normal_working_sites()
    print("\nAll generic fallback adapter tests passed.")
