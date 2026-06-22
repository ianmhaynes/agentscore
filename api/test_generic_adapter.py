"""Tests for GenericFallbackAdapter — the last-resort, low-confidence
catch-all adapter, built from live inspection of Belle Property (June 2026)
but designed to be tried broadly across unrecognized agency sites."""
import sys
sys.path.insert(0, ".")
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


if __name__ == "__main__":
    test_adapter_order_generic_is_last()
    test_detect_always_true()
    test_active_listing_parsed_via_confirmed_classes()
    test_sold_status_determined_by_url_not_page_text()
    test_active_url_without_sold_segment_stays_active()
    test_missing_address_returns_none_not_garbage()
    test_looks_like_listing_url_heuristic()
    test_collect_listing_urls_finds_homepage_embedded_listings()
    test_listing_url_heuristic_real_world_confirmed_urls()
    print("\nAll generic fallback adapter tests passed.")
