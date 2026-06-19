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
    Fixtures shaped directly from live raw HTML inspection of real
    Harcourts Property Hub listing pages (June 2026, via `requests.get`,
    not a rendered/markdown view). Confirmed real structure:
      - <p class="fw-bold mb-0">Property for Sale</p> / "Sold Property"
      - <h1>{address}</h1>
      - <h3>{price}</h3>  <- bare h3, NO class. Several OTHER h3 tags with
        classes exist on the page (e.g. class="display-1 mb-0" repeating
        the address) — a naive <h3> search matches those first, which was
        a real bug found via raw HTML inspection and is covered here.
      - <p class="agent-office">{office name}</p>  <- confirmed dedicated
        class; previously matched a font preload tag instead, also a
        real bug found and fixed.
    """
    from scraper import CloudhiRexAdapter

    fake_active_detail = """
    <html><head>
    <link rel="preload" href="https://resources.cloudhi.io/fonts/Harcourts-Script.woff2" as="font" type="font/woff2" crossorigin="anonymous">
    </head><body>
    <p class="fw-bold mb-0">Property for Sale</p>
    <h1>5/13 Mapleton Circuit, Varsity Lakes, QLD 4227</h1>
    <h3 class="display-1 mb-0">5/13 Mapleton Circuit, Varsity Lakes, QLD 4227</h3>
    <h3 class="text-cyan fw-light mb-0">Open for Inspection</h3>
    <h3>Offers Over $979,000</h3>
    <a href="/property-hub/people/george-may-2" class="text-decoration-none">
      <img alt="George May">
    </a>
    <a href="/property-hub/people/george-may-2" class="text-decoration-none">
      <p class="agent-name">George May</p>
    </a>
    <p class="agent-office">Harcourts Property Hub - Robina</p>
    </body></html>
    """
    fake_sold_detail = """
    <html><head>
    <link rel="preload" href="https://resources.cloudhi.io/fonts/Harcourts-Script.woff2" as="font" type="font/woff2" crossorigin="anonymous">
    </head><body>
    <p class="fw-bold mb-0">Sold Property</p>
    <h1>35/19 Carina Peak Drive, Varsity Lakes, QLD 4227</h1>
    <h3 class="display-1 mb-0">35/19 Carina Peak Drive, Varsity Lakes, QLD 4227</h3>
    <h3>$925,000</h3>
    <a href="/property-hub/people/mitch-harrop" class="text-decoration-none">
      <img alt="Mitch Harrop">
    </a>
    <a href="/property-hub/people/mitch-harrop" class="text-decoration-none">
      <p class="agent-name">Mitch Harrop</p>
    </a>
    <p class="agent-office">Harcourts Property Hub - Robina</p>
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
    assert active.guide_price == "979000", f"FAIL: got {active.guide_price!r} (likely matched wrong h3)"
    assert active.sold_price == ""
    assert active.agent_name == "George May"
    assert active.office_name == "Harcourts Property Hub - Robina", f"FAIL: got {active.office_name!r} (likely matched font preload)"
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
    assert sold.office_name == "Harcourts Property Hub - Robina"

    print("PASS: CloudhiRexAdapter parses confirmed detail-page structure correctly")
    print("PASS: correctly avoids decoy <h3> tags and font-preload office name bugs")
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
    <p class="agent-name">George May</p>
    <p class="agent-office">Harcourts Property Hub - Robina</p>
    </body></html>
    """
    fake_sold_detail = """
    <html><body>
    <p class="fw-bold mb-0">Sold Property</p>
    <h1>35/19 Carina Peak Drive, Varsity Lakes, QLD 4227</h1>
    <h3>$925,000</h3>
    <a href="/property-hub/people/mitch-harrop"><img alt="Mitch Harrop"></a>
    <p class="agent-name">Mitch Harrop</p>
    <p class="agent-office">Harcourts Property Hub - Robina</p>
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


def test_cloudhi_dates_and_days_on_market():
    """
    Confirmed via live raw HTML inspection (June 2026):
      - Active listings: plain text "Added 17 June, 2026" near Property ID
      - Sold listings:   <h3>Sold Date</h3> followed by a nested
        <div class="col">16 June, 2026</div>
    """
    from scraper import CloudhiRexAdapter

    fake_active = """
    <html><body>
    <p class="fw-bold mb-0">Property for Sale</p>
    <h1>62/1 Bridgman Drive, Reedy Creek, QLD 4227</h1>
    <h3 class="display-1 mb-0">62/1 Bridgman Drive, Reedy Creek, QLD 4227</h3>
    <h3>Offers over $899,000</h3>
    <a href="/property-hub/people/peter-boxsell"><img alt="Peter Boxsell"></a>
    <p class="agent-name">Peter Boxsell</p>
    <p class="agent-office">Harcourts Property Hub - Robina</p>
    Added 17 June, 2026</small></span>
    <span class="property-id-text mb-4">Property ID: #R2-5117315</span>
    </body></html>
    """
    fake_sold = """
    <html><body>
    <p class="fw-bold mb-0">Sold Property</p>
    <h1>65A/1-7 Ridgevista Court, Reedy Creek, QLD 4227</h1>
    <h3 class="display-1 mb-0">65A/1-7 Ridgevista Court, Reedy Creek, QLD 4227</h3>
    <h3>$935,000</h3>
    <a href="/property-hub/people/raymond-pienaar"><img alt="Raymond Pienaar"></a>
    <p class="agent-name">Raymond Pienaar</p>
    <p class="agent-office">Harcourts Property Hub - Robina</p>
    Added 1 March, 2026</small></span>
    <h3>Sold Date</h3>
    <div class="row g-0"><div class="col"><ul class="list-unstyled my-xxl-0">
    <li class="list-item g-0 align-items-center"><div class="col">16 June, 2026</div></li>
    </ul></div></div>
    </body></html>
    """

    adapter = CloudhiRexAdapter()
    logs = []
    active = adapter._parse_detail_page(
        fake_active, "https://propertyhub.harcourts.com.au/listing/test1",
        "https://propertyhub.harcourts.com.au", logs.append
    )
    assert active.date_listed == "2026-06-17", f"FAIL: {active.date_listed!r}"
    assert active.sold_date == ""

    logs2 = []
    sold = adapter._parse_detail_page(
        fake_sold, "https://propertyhub.harcourts.com.au/listing/test2",
        "https://propertyhub.harcourts.com.au", logs2.append
    )
    assert sold.date_listed == "2026-03-01", f"FAIL: {sold.date_listed!r}"
    assert sold.sold_date == "2026-06-16", f"FAIL: {sold.sold_date!r}"
    assert sold.days_on_market == "107", f"FAIL: {sold.days_on_market!r}"  # confirmed: 1 Mar -> 16 Jun = 107 days

    print("PASS: CloudhiRexAdapter correctly extracts Added/Sold Date and computes days_on_market")
    print(f"  Active row date_listed: {active.date_listed}")
    print(f"  Sold row date_listed/sold_date/days_on_market: {sold.date_listed} / {sold.sold_date} / {sold.days_on_market}")


def test_calculate_days_on_market():
    from scraper import calculate_days_on_market
    # Sold case: fixed start and end
    assert calculate_days_on_market("2026-03-01", "2026-06-16") == "107"
    # Missing date_listed: should return "" not crash
    assert calculate_days_on_market("", "2026-06-16") == ""
    # Malformed date: should return "" not crash
    assert calculate_days_on_market("not-a-date", "2026-06-16") == ""
    # Active (no end date) — just confirm it returns a non-negative number, not the exact value (depends on "today")
    result = calculate_days_on_market("2026-06-01")
    assert result.isdigit(), f"FAIL: expected a digit string, got {result!r}"
    print("PASS: calculate_days_on_market handles sold, missing, malformed, and active cases correctly")


if __name__ == "__main__":
    test_extract_initial_state()
    test_detect()
    test_normalize_active()
    test_normalize_sold()
    test_full_fetch_logic_with_monkeypatch()
    test_cloudhi_detect_and_reject()
    test_cloudhi_detail_page_parsing()
    test_cloudhi_full_fetch()
    test_cloudhi_dates_and_days_on_market()
    test_calculate_days_on_market()
    print("\nAll tests passed.")
