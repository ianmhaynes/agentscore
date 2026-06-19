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
    ray_white_html = '<html>window.INITIAL_STATE = {}; site uses raywhite branding</html>'
    unrelated_html = "<html><body>nothing here</body></html>"
    assert adapter.detect(cloudhi_html), "Should detect cloudhi.io marker"
    assert not adapter.detect(unrelated_html), "Should not false-positive on unrelated HTML"
    print("PASS: CloudhiRexAdapter.detect() correctly identifies Cloudhi pages")


def test_cloudhi_card_parsing():
    from scraper import CloudhiRexAdapter

    fake_buy_html = """
    <html><head><link rel="preload" href="https://resources.cloudhi.io/css/main.css"></head>
    <body>
    <div class="listing-card">
    <a href="https://propertyhub.harcourts.com.au/listing/r2-5119238-5-13-mapleton-circuit-varsity-lakes-qld-4227"></a>
    <a href="https://propertyhub.harcourts.com.au/property-hub/people/mitch-harrop"><img alt="Mitch Harrop"></a>
    <h6><a href="...">Vacant Tri-Level Townhouse</a></h6>
    Offers Over $979,000 5/13 Mapleton Circuit, Varsity Lakes, QLD 4227
    </div>
    <div class="listing-card">
    <a href="https://propertyhub.harcourts.com.au/listing/r2-4852701-117-aylesham-drive-bonogin-qld-4213"></a>
    <a href="https://propertyhub.harcourts.com.au/property-hub/people/talei-kelly"><img alt="Talei Kelly"></a>
    <h6><a href="...">Auction Property</a></h6>
    AUCTION 117 Aylesham Drive, Bonogin, QLD 4213
    </div>
    </body></html>
    """
    fake_sold_html = """
    <html><head><script src="https://assets.cloudhi.io/x.js"></script></head>
    <body>
    <div class="listing-card">
    <a href="https://propertyhub.harcourts.com.au/listing/r2-1111111-1-test-street-robina-qld-4226"></a>
    <a href="https://propertyhub.harcourts.com.au/property-hub/people/jane-doe"><img alt="Jane Doe"></a>
    <h6><a href="...">Sold Test Property</a></h6>
    $850,000 1 Test Street, Robina, QLD 4226
    </div>
    </body></html>
    """

    adapter = CloudhiRexAdapter()
    logs = []
    active = adapter._parse_cards_from_html(fake_buy_html, "https://propertyhub.harcourts.com.au", "Active", "active", logs.append)
    assert len(active) == 2
    assert active[0].guide_price == "979000"
    assert active[0].address == "5/13 Mapleton Circuit, Varsity Lakes, QLD 4227"
    assert "$" not in active[0].address, "Address must not contain stray price digits"
    assert active[1].guide_price == "", "AUCTION listing should have no parsed numeric price"
    assert active[0].extraction_confidence == "medium", "Cloudhi adapter must mark medium confidence"

    logs2 = []
    sold = adapter._parse_cards_from_html(fake_sold_html, "https://propertyhub.harcourts.com.au", "Sold", "sold", logs2.append)
    assert len(sold) == 1
    assert sold[0].sold_price == "850000"
    assert sold[0].guide_price == "", "Sold row should not populate guide_price"

    print("PASS: CloudhiRexAdapter parses active and sold listing cards correctly")
    print(f"  Sample active row: {active[0]}")
    print(f"  Sample sold row:   {sold[0]}")


if __name__ == "__main__":
    test_extract_initial_state()
    test_detect()
    test_normalize_active()
    test_normalize_sold()
    test_full_fetch_logic_with_monkeypatch()
    test_cloudhi_detect_and_reject()
    test_cloudhi_card_parsing()
    print("\nAll tests passed.")
