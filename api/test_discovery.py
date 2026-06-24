"""Tests for discovery.py — Google Places API office discovery."""
import sys
sys.path.insert(0, ".")
import discovery as discovery_module


def test_pagination_follows_nextPageToken_across_multiple_pages():
    """
    Confirmed real Google Places API behavior (per official docs):
    Text Search returns a nextPageToken when more results exist beyond
    the current page; passing it back as `pageToken` in a follow-up
    request fetches the next page. This test confirms discover_agencies
    correctly follows that chain across 3 pages, stopping once a
    response has no nextPageToken.
    """
    class FakeResponse:
        def __init__(self, json_data, status_code=200):
            self._json = json_data
            self.status_code = status_code
            self.text = str(json_data)
        def json(self):
            return self._json

    def make_page(n, has_next):
        places = [
            {"displayName": {"text": f"Agency {n}-{i}"}, "id": f"id-{n}-{i}"}
            for i in range(20)
        ]
        resp = {"places": places}
        if has_next:
            resp["nextPageToken"] = f"token-for-page-{n + 1}"
        return resp

    pages_requested = []

    def fake_post(url, json=None, headers=None, timeout=None):
        page_token = json.get("pageToken")
        pages_requested.append(page_token)
        if page_token is None:
            return FakeResponse(make_page(1, has_next=True))
        elif page_token == "token-for-page-2":
            return FakeResponse(make_page(2, has_next=True))
        elif page_token == "token-for-page-3":
            return FakeResponse(make_page(3, has_next=False))
        return FakeResponse({"places": []})

    def fake_get(url, headers=None, timeout=None):
        return FakeResponse({"websiteUri": "https://example.com.au"})

    original_post, original_get = discovery_module.requests.post, discovery_module.requests.get
    original_sleep = discovery_module.time.sleep
    discovery_module.requests.post = fake_post
    discovery_module.requests.get = fake_get
    discovery_module.time.sleep = lambda s: None  # skip the real delay in tests
    try:
        logs = []
        result = discovery_module.discover_agencies(
            "Gold Coast QLD", api_key="fake-key", log=logs.append, max_pages=5
        )
        assert len(pages_requested) == 3, f"FAIL: expected 3 page requests, got {len(pages_requested)}"
        assert pages_requested[0] is None
        assert pages_requested[1] == "token-for-page-2"
        assert pages_requested[2] == "token-for-page-3"
        assert len(result) == 60, f"FAIL: expected 60 agencies (3 pages x 20), got {len(result)}"
        print("PASS: pagination correctly follows nextPageToken across multiple pages, "
              "stopping once a page has none")
    finally:
        discovery_module.requests.post = original_post
        discovery_module.requests.get = original_get
        discovery_module.time.sleep = original_sleep


def test_pagination_respects_max_pages_safety_cap():
    """
    Even if Google's API keeps returning a nextPageToken indefinitely
    (a genuinely huge area), discover_agencies must stop at max_pages
    rather than keep paginating forever and running up real API costs.
    """
    class FakeResponse:
        def __init__(self, json_data, status_code=200):
            self._json = json_data
            self.status_code = status_code
            self.text = str(json_data)
        def json(self):
            return self._json

    call_count = {"n": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        call_count["n"] += 1
        # ALWAYS return a nextPageToken, simulating an enormous area
        # that genuinely has more results than any reasonable cap
        return FakeResponse({
            "places": [{"displayName": {"text": f"Agency {call_count['n']}"}, "id": f"id-{call_count['n']}"}],
            "nextPageToken": "always-more",
        })

    def fake_get(url, headers=None, timeout=None):
        return FakeResponse({"websiteUri": "https://example.com.au"})

    original_post, original_get = discovery_module.requests.post, discovery_module.requests.get
    original_sleep = discovery_module.time.sleep
    discovery_module.requests.post = fake_post
    discovery_module.requests.get = fake_get
    discovery_module.time.sleep = lambda s: None
    try:
        logs = []
        result = discovery_module.discover_agencies(
            "A Truly Enormous Area", api_key="fake-key", log=logs.append, max_pages=3
        )
        assert call_count["n"] == 3, f"FAIL: should stop at exactly max_pages=3 calls, got {call_count['n']}"
        assert len(result) == 3
        assert any("max_pages" in l for l in logs), "FAIL: should log that the cap was hit"
        print("PASS: pagination respects the max_pages safety cap, even when Google keeps "
              "returning more pages, protecting against runaway API costs")
    finally:
        discovery_module.requests.post = original_post
        discovery_module.requests.get = original_get
        discovery_module.time.sleep = original_sleep


def test_no_api_key_returns_empty_gracefully():
    logs = []
    result = discovery_module.discover_agencies("Mermaid Waters QLD 4218", api_key=None, log=logs.append)
    assert result == []
    assert any("No Google Places API key" in l for l in logs)
    print("PASS: missing API key handled gracefully, no crash")


def test_full_flow_with_mock():
    """
    Fixtures shaped from Google's documented Places API (New) response
    structure for Text Search and Place Details, confirmed via official
    docs (developers.google.com/maps/documentation/places/web-service).
    """
    class FakeResponse:
        def __init__(self, json_data, status_code=200, text=""):
            self._json = json_data
            self.status_code = status_code
            self.text = text or str(json_data)
        def json(self):
            return self._json

    text_search_response = {
        "places": [
            {
                "displayName": {"text": "Ray White Mermaid Waters"},
                "id": "ChIJYySQY2AFkWsRDtx1TApHN0M",
                "formattedAddress": "14/90 Markeri St, Mermaid Waters QLD 4218",
            },
            {
                "displayName": {"text": "Harcourts Property Hub"},
                "id": "ChIJabc123",
                "formattedAddress": "Robina QLD 4226",
            },
        ]
    }
    place_details_responses = {
        "ChIJYySQY2AFkWsRDtx1TApHN0M": {"websiteUri": "https://raywhitemermaidwaters.com.au/"},
        "ChIJabc123": {"websiteUri": "https://propertyhub.harcourts.com.au/"},
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        assert "searchText" in url
        assert headers["X-Goog-Api-Key"] == "fake-key-123"
        return FakeResponse(text_search_response)

    def fake_get(url, headers=None, timeout=None):
        place_id = url.split("/")[-1]
        return FakeResponse(place_details_responses.get(place_id, {}))

    original_post, original_get = discovery_module.requests.post, discovery_module.requests.get
    discovery_module.requests.post = fake_post
    discovery_module.requests.get = fake_get
    try:
        logs = []
        result = discovery_module.discover_agencies(
            "Mermaid Waters QLD 4218", api_key="fake-key-123", log=logs.append
        )
        assert len(result) == 2
        rw = next(a for a in result if a["name"] == "Ray White Mermaid Waters")
        hc = next(a for a in result if a["name"] == "Harcourts Property Hub")
        assert rw["website"] == "https://raywhitemermaidwaters.com.au/"
        assert hc["website"] == "https://propertyhub.harcourts.com.au/"
        print("PASS: full discover_agencies flow works with confirmed Places API response shape")
    finally:
        discovery_module.requests.post = original_post
        discovery_module.requests.get = original_get


def test_text_search_failure_returns_empty_gracefully():
    class FakeResponse:
        def __init__(self, status_code, text="error"):
            self.status_code = status_code
            self.text = text
        def json(self):
            return {}

    def fake_post(url, json=None, headers=None, timeout=None):
        return FakeResponse(403, "API key not authorized")

    original_post = discovery_module.requests.post
    discovery_module.requests.post = fake_post
    try:
        logs = []
        result = discovery_module.discover_agencies("Mermaid Waters QLD 4218", api_key="bad-key", log=logs.append)
        assert result == []
        assert any("403" in l for l in logs), "Should log the real HTTP status for diagnosis"
        print("PASS: Text Search failure (e.g. bad API key) handled gracefully")
    finally:
        discovery_module.requests.post = original_post


def test_place_details_failure_for_one_agency_does_not_break_others():
    class FakeTextResponse:
        def __init__(self):
            self.status_code = 200
            self.text = ""
        def json(self):
            return {"places": [
                {"displayName": {"text": "Agency A"}, "id": "place_a"},
                {"displayName": {"text": "Agency B"}, "id": "place_b"},
            ]}

    class FakeDetailsResponse:
        def __init__(self, status_code, website=None):
            self.status_code = status_code
            self.text = ""
            self._website = website
        def json(self):
            return {"websiteUri": self._website} if self._website else {}

    def fake_post(url, json=None, headers=None, timeout=None):
        return FakeTextResponse()

    def fake_get(url, headers=None, timeout=None):
        if "place_a" in url:
            return FakeDetailsResponse(500)  # this one fails
        return FakeDetailsResponse(200, website="https://agencyb.example.com")

    original_post, original_get = discovery_module.requests.post, discovery_module.requests.get
    discovery_module.requests.post = fake_post
    discovery_module.requests.get = fake_get
    try:
        logs = []
        result = discovery_module.discover_agencies("Test Area", api_key="fake-key", log=logs.append)
        assert len(result) == 2, "Both agencies should still appear in results"
        agency_a = next(a for a in result if a["name"] == "Agency A")
        agency_b = next(a for a in result if a["name"] == "Agency B")
        assert agency_a["website"] is None, "Failed lookup should be None, not crash the whole run"
        assert agency_b["website"] == "https://agencyb.example.com"
        print("PASS: one agency's Place Details failure doesn't break the rest of the run")
    finally:
        discovery_module.requests.post = original_post
        discovery_module.requests.get = original_get


if __name__ == "__main__":
    test_pagination_follows_nextPageToken_across_multiple_pages()
    test_pagination_respects_max_pages_safety_cap()
    test_no_api_key_returns_empty_gracefully()
    test_full_flow_with_mock()
    test_text_search_failure_returns_empty_gracefully()
    test_place_details_failure_for_one_agency_does_not_break_others()
    print("\nAll discovery tests passed.")
