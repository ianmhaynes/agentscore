"""Tests for discovery.py — Domain.com.au agency discovery."""
import sys
sys.path.insert(0, ".")
from discovery import (
    _extract_agency_cards, _extract_total_pages,
    _extract_agency_website, _slugify_suburb_postcode,
)


def test_slugify():
    assert _slugify_suburb_postcode("Mermaid Waters QLD 4218") == "mermaid-waters-qld-4218"
    assert _slugify_suburb_postcode("mermaid-waters-qld-4218") == "mermaid-waters-qld-4218"
    print("PASS: slugify handles both human and slug input forms")


def test_extract_agency_cards():
    html = """
    <a href="/real-estate-agencies/kollosche-31985/">View Kollosche's profile</a>
    <a href="/real-estate-agencies/raywhitemermaidwaters-34455/">View Ray White Mermaid Waters's profile</a>
    """
    cards = _extract_agency_cards(html)
    assert len(cards) == 2
    assert cards[0]["name"] == "Kollosche"
    assert "raywhitemermaidwaters-34455" in cards[1]["domain_profile_url"]
    print("PASS: agency cards extracted with correct name and URL")


def test_extract_agency_cards_deduplicates():
    html = """
    <a href="/real-estate-agencies/kollosche-31985/">View Kollosche's profile</a>
    <a href="/real-estate-agencies/kollosche-31985/">View Kollosche's profile</a>
    """
    cards = _extract_agency_cards(html)
    assert len(cards) == 1, "Duplicate agency links should be deduplicated"
    print("PASS: duplicate agency cards are deduplicated")


def test_extract_total_pages():
    html = '<a href="?page=2">2</a><a href="?page=8">8</a>'
    assert _extract_total_pages(html) == 8
    assert _extract_total_pages("<html>no pagination here</html>") == 1
    print("PASS: pagination max-page detection works, defaults to 1 if absent")


def test_extract_agency_website():
    html = '<a href="http://www.raywhitemermaidwaters.com.au">link</a>'
    assert _extract_agency_website(html) == "http://www.raywhitemermaidwaters.com.au"
    print("PASS: agency website extracted from confirmed link pattern")


def test_extract_agency_website_returns_none_when_absent():
    html = "<html><body>no external links here</body></html>"
    assert _extract_agency_website(html) is None
    print("PASS: returns None gracefully when no website link present (not a crash)")


def test_discover_agencies_full_flow_with_mock():
    import discovery as discovery_module

    class FakeResponse:
        def __init__(self, text, status_code=200):
            self.text = text
            self.status_code = status_code

    page1 = """
    <a href="/real-estate-agencies/raywhitemermaidwaters-34455/">View Ray White Mermaid Waters's profile</a>
    <a href="?page=2">2</a>
    """
    page2 = '<a href="/real-estate-agencies/kollosche-31985/">View Kollosche\'s profile</a>'
    rw_profile = '<a href="http://www.raywhitemermaidwaters.com.au">link</a>'
    kollosche_profile = "<html>no website here</html>"

    class FakeSession:
        def __init__(self):
            self.headers = {}
        def get(self, url, timeout=None):
            if "page=2" in url:
                return FakeResponse(page2)
            elif "raywhitemermaidwaters-34455" in url:
                return FakeResponse(rw_profile)
            elif "kollosche-31985" in url:
                return FakeResponse(kollosche_profile)
            elif "real-estate-agencies/mermaid" in url:
                return FakeResponse(page1)
            return FakeResponse("", status_code=404)

    original_session = discovery_module.requests.Session
    discovery_module.requests.Session = FakeSession
    try:
        logs = []
        result = discovery_module.discover_agencies("Mermaid Waters QLD 4218", log=logs.append)
        assert len(result) == 2
        rw = next(a for a in result if a["name"] == "Ray White Mermaid Waters")
        assert rw["website"] == "http://www.raywhitemermaidwaters.com.au"
        kol = next(a for a in result if a["name"] == "Kollosche")
        assert kol["website"] is None
        print("PASS: full discover_agencies flow works end-to-end with pagination")
    finally:
        discovery_module.requests.Session = original_session


def test_discover_agencies_handles_blocked_directory():
    """If Domain blocks the directory page itself (e.g. 403), should
    return an empty list and log clearly, not crash."""
    import discovery as discovery_module

    class FakeResponse:
        def __init__(self, status_code):
            self.text = ""
            self.status_code = status_code

    class FakeBlockedSession:
        def __init__(self):
            self.headers = {}
        def get(self, url, timeout=None):
            return FakeResponse(403)

    original_session = discovery_module.requests.Session
    discovery_module.requests.Session = FakeBlockedSession
    try:
        logs = []
        result = discovery_module.discover_agencies("Mermaid Waters QLD 4218", log=logs.append)
        assert result == [], "Should return empty list, not crash, when directory page is blocked"
        assert any("403" in l for l in logs), "Should log the real HTTP status for diagnosis"
        print("PASS: gracefully handles a blocked/403 directory page without crashing")
    finally:
        discovery_module.requests.Session = original_session


if __name__ == "__main__":
    test_slugify()
    test_extract_agency_cards()
    test_extract_agency_cards_deduplicates()
    test_extract_total_pages()
    test_extract_agency_website()
    test_extract_agency_website_returns_none_when_absent()
    test_discover_agencies_full_flow_with_mock()
    test_discover_agencies_handles_blocked_directory()
    print("\nAll discovery tests passed.")
