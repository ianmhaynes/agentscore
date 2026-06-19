"""
Test the parsing logic against a fake INITIAL_STATE blob shaped exactly
like the real one we confirmed live (Ray White Mermaid Waters, June 2026).
This doesn't test network access (can't reach real sites from here) but
DOES verify the extraction/normalization logic is correct.
"""
import json
import sys
sys.path.insert(0, ".")
from scraper import extract_initial_state, RayWhiteDynamicsAdapter

FAKE_ACTIVE_HTML = """
<html><body><script>
window.INITIAL_STATE = {"listings":{"entities":{"3522998":{
  "listingId":3522998,"status":"Active","statusCode":"CUR",
  "price":"1300000","displayPrice":"AUCTION",
  "soldPrice":null,"soldDate":null,
  "creationTime":"2026-06-10T11:21:29.377+10:00",
  "address":{"formatted":"12 Saunders Drive\\nBonogin  Queensland  4213\\nAustralia",
             "suburb":"Bonogin","postCode":"4213"},
  "office":{"businessName":"Ray White Mermaid Waters"},
  "agents":[{"fullName":"Ben Gannon","email":"ben.gannon@raywhite.com",
             "mobilePhone":"0427797752","memberId":128934}]
}}}};
</script></body></html>
"""

FAKE_SOLD_HTML = """
<html><body><script>
window.INITIAL_STATE = {"listings":{"entities":{"3465123":{
  "listingId":3465123,"status":"Sold","statusCode":"SLD",
  "price":"400000","displayPrice":"CONTACT AGENT",
  "soldPrice":465000,"soldDate":"2026-04-14",
  "creationTime":"2026-03-04T13:45:19.47+10:00",
  "address":{"formatted":"4/29 Leonard Avenue\\nSurfers Paradise  Queensland  4217\\nAustralia",
             "suburb":"Surfers Paradise","postCode":"4217"},
  "office":{"businessName":"Ray White Mermaid Waters"},
  "agents":[{"fullName":"Test Agent","email":"test@raywhite.com",
             "mobilePhone":"0400000000","memberId":99999}]
}}}};
</script></body></html>
"""

def test_extract_initial_state():
    state = extract_initial_state(FAKE_ACTIVE_HTML)
    assert state is not None, "Failed to extract INITIAL_STATE"
    assert "3522998" in state["listings"]["entities"]
    print("PASS: extract_initial_state finds and parses JSON correctly")

def test_detect():
    adapter = RayWhiteDynamicsAdapter()
    assert adapter.detect(FAKE_ACTIVE_HTML), "Adapter failed to detect valid Ray White HTML"
    assert not adapter.detect("<html>just a normal page</html>"), "Adapter false-positived on unrelated HTML"
    print("PASS: detect() correctly identifies Ray White pages and rejects others")

def test_normalize_active():
    state = extract_initial_state(FAKE_ACTIVE_HTML)
    entities = state["listings"]["entities"]
    e = entities["3522998"]
    assert e["statusCode"] == "CUR"
    assert e["price"] == "1300000"
    assert e["soldPrice"] is None
    assert e["address"]["suburb"] == "Bonogin"
    assert e["agents"][0]["fullName"] == "Ben Gannon"
    print("PASS: active listing fields match expected structure")

def test_normalize_sold():
    state = extract_initial_state(FAKE_SOLD_HTML)
    entities = state["listings"]["entities"]
    e = entities["3465123"]
    assert e["statusCode"] == "SLD"
    assert e["soldPrice"] == 465000
    assert e["soldDate"] == "2026-04-14"
    print("PASS: sold listing fields match expected structure")

class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class FakeSession:
    """Module-level fixture (importable elsewhere) standing in for
    requests.Session, returning fake INITIAL_STATE HTML without any
    real network access."""
    def __init__(self):
        self.headers = {}
    def get(self, url, timeout=None):
        if "for-sale" in url:
            return FakeResponse(FAKE_ACTIVE_HTML)
        elif "sold" in url:
            return FakeResponse(FAKE_SOLD_HTML)
        elif "fakeoffice" in url:
            # homepage check in scrape_office() — just needs detect() to pass
            return FakeResponse(FAKE_ACTIVE_HTML)
        return FakeResponse("", status_code=404)


def test_full_fetch_logic_with_monkeypatch():
    """Simulate the fetch() method's parsing logic without real HTTP calls."""
    import scraper as scraper_module

    original_session = scraper_module.requests.Session
    scraper_module.requests.Session = FakeSession

    try:
        adapter = RayWhiteDynamicsAdapter()
        logs = []
        listings = adapter.fetch("https://fakeoffice.example.com", log=logs.append)

        assert len(listings) == 2, f"Expected 2 listings, got {len(listings)}"

        active = [l for l in listings if l.status == "Active"]
        sold = [l for l in listings if l.status == "Sold"]
        assert len(active) == 1, "Expected 1 active listing"
        assert len(sold) == 1, "Expected 1 sold listing"

        a = active[0]
        assert a.guide_price == "1300000"
        assert a.agent_name == "Ben Gannon"
        assert a.suburb == "Bonogin"

        s = sold[0]
        assert s.sold_price == "465000"
        assert s.sold_date == "2026-04-14"

        print("PASS: full fetch() logic correctly produces normalized Listing objects")
        print(f"  Sample active row: {a}")
        print(f"  Sample sold row:   {s}")
    finally:
        scraper_module.requests.Session = original_session

def test_cloudhi_detect_and_reject():
    from scraper import CloudhiRexAdapter
    adapter = CloudhiRexAdapter()
    cloudhi_html = '<html><head><link href="https://resources.cloudhi.io/css/main.css"></head></html>'
    unrelated_html = "<html><body>nothing here</body></html>"
    assert adapter.detect(cloudhi_html), "Should detect cloudhi.io marker"
    assert not adapter.detect(unrelated_html), "Should not false-positive on unrelated HTML"
    print("PASS: CloudhiRexAdapter.detect() correctly identifies Cloudhi pages")


def test_cloudhi_detail_page_parsing():
    """
    Fixtures shaped directly from live DevTools inspection of real
    Harcourts Property Hub listing pages (June 2026):
      - Active: propertyhub.harcourts.com.au/listing/r2-5119238-...
      - Sold:   a sold listing on the same site
    Confirmed real tags: <p class="fw-bold mb-0">, <h1>, <h3>.
    """
    from scraper import CloudhiRexAdapter

    fake_active_detail = """
    <html><body>
    <p class="fw-bold mb-0">Property for Sale</p>
    <h1>5/13 Mapleton Circuit, Varsity Lakes, QLD 4227</h1>
    <h3>Offers Over $979,000</h3>
    <a href="/property-hub/people/george-may-2"><img alt="George May"></a>
    George May
    Harcourts Property Hub - Robina
    </body></html>
    """
    fake_sold_detail = """
    <html><body>
    <p class="fw-bold mb-0">Sold Property</p>
    <h1>35/19 Carina Peak Drive, Varsity Lakes, QLD 4227</h1>
    <h3>$925,000</h3>
    <a href="/property-hub/people/mitch-harrop"><img alt="Mitch Harrop"></a>
    Mitch Harrop
    Harcourts Property Hub - Robina
    </body></html>
    """

    adapter = CloudhiRexAdapter()
    logs = []
    active = adapter._parse_detail_page(
        fake_active_detail, "https://propertyhub.harcourts.com.au/listing/r2-5119238-test",
        "https://propertyhub.harcourts.com.au", logs.append
    )
    assert active.status == "Active"
    assert active.address == "5/13 Mapleton Circuit, Varsity Lakes, QLD 4227"
    assert active.guide_price == "979000"
    assert active.sold_price == ""
    assert active.agent_name == "George May"
    assert active.extraction_confidence == "medium"

    logs2 = []
    sold = adapter._parse_detail_page(
        fake_sold_detail, "https://propertyhub.harcourts.com.au/listing/r2-1111111-test",
        "https://propertyhub.harcourts.com.au", logs2.append
    )
    assert sold.status == "Sold"
    assert sold.address == "35/19 Carina Peak Drive, Varsity Lakes, QLD 4227"
    assert sold.sold_price == "925000"
    assert sold.guide_price == ""
    assert sold.agent_name == "Mitch Harrop"

    print("PASS: CloudhiRexAdapter parses confirmed detail-page structure correctly")
    print(f"  Sample active row: {active}")
    print(f"  Sample sold row:   {sold}")


def test_cloudhi_full_fetch():
    """End-to-end test of the two-step index-then-detail fetch flow."""
    from scraper import CloudhiRexAdapter
    import scraper as scraper_module

    fake_index_buy = """
    <html><head><link href="https://resources.cloudhi.io/css/main.css"></head>
    <body><a href="https://propertyhub.harcourts.com.au/listing/r2-5119238-test">card</a></body></html>
    """
    fake_index_sold = """
    <html><body><a href="https://propertyhub.harcourts.com.au/listing/r2-1111111-test">card</a></body></html>
    """
    fake_active_detail = """
    <html><body>
    <p class="fw-bold mb-0">Property for Sale</p>
    <h1>5/13 Mapleton Circuit, Varsity Lakes, QLD 4227</h1>
    <h3>Offers Over $979,000</h3>
    <a href="/property-hub/people/george-may-2"><img alt="George May"></a>
    George May
    Harcourts Property Hub - Robina
    </body></html>
    """
    fake_sold_detail = """
    <html><body>
    <p class="fw-bold mb-0">Sold Property</p>
    <h1>35/19 Carina Peak Drive, Varsity Lakes, QLD 4227</h1>
    <h3>$925,000</h3>
    <a href="/property-hub/people/mitch-harrop"><img alt="Mitch Harrop"></a>
    Mitch Harrop
    Harcourts Property Hub - Robina
    </body></html>
    """

    class FakeCloudhiSession:
        def __init__(self):
            self.headers = {}
        def get(self, url, timeout=None):
            if url.endswith("/listings/buy"):
                return FakeResponse(fake_index_buy)
            elif url.endswith("/listings/sold"):
                return FakeResponse(fake_index_sold)
            elif "r2-5119238" in url:
                return FakeResponse(fake_active_detail)
            elif "r2-1111111" in url:
                return FakeResponse(fake_sold_detail)
            return FakeResponse("", status_code=404)

    original_session = scraper_module.requests.Session
    scraper_module.requests.Session = FakeCloudhiSession
    try:
        adapter = CloudhiRexAdapter()
        logs = []
        listings = adapter.fetch("https://propertyhub.harcourts.com.au", logs.append)
        assert len(listings) == 2
        active = [l for l in listings if l.status == "Active"][0]
        sold = [l for l in listings if l.status == "Sold"][0]
        assert active.guide_price == "979000"
        assert sold.sold_price == "925000"
        print("PASS: CloudhiRexAdapter full fetch() flow (index -> detail pages) works end-to-end")
    finally:
        scraper_module.requests.Session = original_session


if __name__ == "__main__":
    test_extract_initial_state()
    test_detect()
    test_normalize_active()
    test_normalize_sold()
    test_full_fetch_logic_with_monkeypatch()
    test_cloudhi_detect_and_reject()
    test_cloudhi_detail_page_parsing()
    test_cloudhi_full_fetch()
    print("\nAll tests passed.")
