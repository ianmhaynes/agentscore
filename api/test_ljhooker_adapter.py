"""
Tests for LJHookerAdapter — confirmed against real live structure of
A706/517 Harris Street, Ultimo NSW (LJ Hooker Pyrmont), inspected via
DevTools and verified via direct fetch, June 2026.
"""
import sys
sys.path.insert(0, ".")
from scraper import LJHookerAdapter, ADAPTERS


FAKE_SOLD_DETAIL = """
<html><body>
<section class="property-overview container--section" itemscope itemtype="https://schema.org/IndividualProduct">
<h2 class="property-overview__address" itemprop="name">A706/517 Harris Street, Ultimo NSW</h2>
<p id="property-information" class="property-overview__status" itemprop="identifier">Sold For $1,670,000</p>
</section>
<h3 class="property-overview__title" itemprop="alternateName">Stunning by day, dazzling by night!</h3>
<div class="description">Contact: John Zheng 0408 330 118</div>
<a href="https://agent.ljhooker.com.au/john-zheng-7351">John Zheng</a>
</body></html>
"""

FAKE_ACTIVE_DETAIL = """
<html><body>
<section class="property-overview container--section" itemscope itemtype="https://schema.org/IndividualProduct">
<h2 class="property-overview__address" itemprop="name">12 Test Street, Sydney NSW</h2>
<p id="property-information" class="property-overview__status" itemprop="identifier">For Sale $899,000</p>
</section>
<div class="description">Contact: Jane Doe 0412 345 678</div>
</body></html>
"""


def test_adapter_registered_before_generic_fallback():
    names = [a.name for a in ADAPTERS]
    assert "lj_hooker" in names
    assert names.index("lj_hooker") < names.index("generic_fallback"), (
        "LJ Hooker is a precise adapter and must be tried before the generic fallback"
    )
    print("PASS: lj_hooker is registered before generic_fallback")


def test_detect():
    adapter = LJHookerAdapter()
    # Confirmed via live fetch: BOTH known LJ Hooker homepage shells
    # (HubSpot-powered, at Broadbeach and Pyrmont alike) contain the
    # searchProfile= URL pattern in their nav links, even though their
    # listing pages live on different downstream platforms. detect()
    # intentionally matches broadly here — fetch() is what determines
    # whether real listing data is actually extractable for a given office.
    homepage_html = '<a href="https://x.ljhooker.com.au/search-results?searchProfile=buy">Buy</a>'
    assert adapter.detect(homepage_html)
    assert not adapter.detect("<html>a totally unrelated real estate site</html>")
    print("PASS: detect() matches on the homepage-present searchProfile= pattern, "
          "not listing-page-only schema markup (a real bug found via live testing)")


def test_sold_listing_parsed_correctly():
    adapter = LJHookerAdapter()
    logs = []
    result = adapter._parse_detail_page(
        FAKE_SOLD_DETAIL,
        "https://property.ljhooker.com.au/residential-ultimo-nsw-apartment-a706-517-harris-street-shcgnz",
        "https://pyrmont.ljhooker.com.au", logs.append,
    )
    assert result.status == "Sold"
    assert result.address == "A706/517 Harris Street, Ultimo NSW"
    assert result.sold_price == "1670000"
    assert result.guide_price == ""
    assert result.agent_name == "John Zheng"
    assert result.agent_phone == "0408 330 118"
    assert result.extraction_confidence == "medium"
    print("PASS: real sold listing (confirmed live data) parsed correctly")
    print(f"  {result}")


def test_active_listing_parsed_correctly():
    adapter = LJHookerAdapter()
    logs = []
    result = adapter._parse_detail_page(
        FAKE_ACTIVE_DETAIL,
        "https://property.ljhooker.com.au/residential-sydney-nsw-house-test123",
        "https://pyrmont.ljhooker.com.au", logs.append,
    )
    assert result.status == "Active"
    assert result.guide_price == "899000"
    assert result.sold_price == ""
    assert result.agent_name == "Jane Doe"
    print("PASS: active listing parsed correctly")


def test_full_fetch_with_office_id_discovery():
    import scraper as scraper_module

    fake_homepage = """
    <a href="https://www.ljhooker.com.au/residential-search-results?officeId=1765&searchProfile=sold">Recent Sales</a>
    """
    fake_buy_index = '<a href="https://property.ljhooker.com.au/residential-sydney-nsw-house-test123">x</a>'
    fake_sold_index = '<a href="https://property.ljhooker.com.au/residential-ultimo-nsw-apartment-a706-517-harris-street-shcgnz">x</a>'

    class FakeResponse:
        def __init__(self, text, status_code=200):
            self.text = text
            self.status_code = status_code

    class FakeSession:
        def __init__(self):
            self.headers = {}
        def get(self, url, timeout=None):
            if "searchProfile=buy" in url:
                return FakeResponse(fake_buy_index)
            elif "searchProfile=sold" in url:
                return FakeResponse(fake_sold_index)
            elif "test123" in url:
                return FakeResponse(FAKE_ACTIVE_DETAIL)
            elif "shcgnz" in url:
                return FakeResponse(FAKE_SOLD_DETAIL)
            return FakeResponse(fake_homepage)

    original_session = scraper_module.requests.Session
    scraper_module.requests.Session = FakeSession
    try:
        adapter = LJHookerAdapter()
        logs = []
        listings = adapter.fetch("https://pyrmont.ljhooker.com.au", logs.append)
        assert len(listings) == 2
        assert any("Found officeId=1765" in l for l in logs)
        active = [l for l in listings if l.status == "Active"][0]
        sold = [l for l in listings if l.status == "Sold"][0]
        assert active.guide_price == "899000"
        assert sold.sold_price == "1670000"
        print("PASS: full fetch() flow with officeId auto-discovery works end-to-end")
    finally:
        scraper_module.requests.Session = original_session


def test_missing_office_id_returns_empty_gracefully():
    """
    Confirmed real-world case: detect() now matches BOTH known LJ Hooker
    homepage shells (since both contain searchProfile= links), but only
    offices whose own search-results pages actually expose a findable
    officeId AND link out to property.ljhooker.com.au-style listing pages
    will yield real data. An office on the JS-loaded platform (or any
    homepage shape we haven't seen) should fail gracefully here, not crash.
    """
    import scraper as scraper_module

    class FakeResponse:
        def __init__(self, text, status_code=200):
            self.text = text
            self.status_code = status_code

    class FakeSessionNoOfficeId:
        def __init__(self):
            self.headers = {}
        def get(self, url, timeout=None):
            return FakeResponse("<html>no officeId anywhere on this page</html>")

    original_session = scraper_module.requests.Session
    scraper_module.requests.Session = FakeSessionNoOfficeId
    try:
        adapter = LJHookerAdapter()
        logs = []
        listings = adapter.fetch("https://example.ljhooker.com.au", logs.append)
        assert listings == [], "Should return empty list, not crash, when officeId can't be found"
        assert any("officeId" in l for l in logs)
        print("PASS: missing officeId handled gracefully (real limitation for the JS-loaded-platform offices)")
    finally:
        scraper_module.requests.Session = original_session


if __name__ == "__main__":
    test_adapter_registered_before_generic_fallback()
    test_detect()
    test_sold_listing_parsed_correctly()
    test_active_listing_parsed_correctly()
    test_full_fetch_with_office_id_discovery()
    test_missing_office_id_returns_empty_gracefully()
    print("\nAll LJ Hooker adapter tests passed.")
