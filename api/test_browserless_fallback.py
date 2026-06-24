"""
Tests for browserless_fallback.py. All HTTP calls are mocked, since
this integration has NOT yet been confirmed against the real live
Browserless API with a real token — these tests verify the request
shape (confirmed correct from Browserless's current documentation,
June 2026) and the module's own error handling, not real-world
behavior. See the module's docstring for the "not yet confirmed live"
caveat that applies here.
"""
import sys
sys.path.insert(0, ".")
import browserless_fallback


def test_no_token_returns_none_gracefully():
    logs = []
    result = browserless_fallback.fetch_rendered_html("https://example.com", api_token=None, log=logs.append)
    assert result is None
    assert any("No API token" in l for l in logs)
    print("PASS: missing API token handled gracefully, no crash")


def test_successful_fetch_returns_html():
    """Fixture shaped from Browserless's own documented /content
    response: plain text/html, the rendered page."""
    class FakeResponse:
        def __init__(self, text, status_code=200):
            self.text = text
            self.status_code = status_code

    real_html = "<html><body>" + ("x" * 500) + "<h4>123 Test St</h4></body></html>"

    def fake_post(url, json=None, headers=None, timeout=None):
        assert "/content" in url
        assert "token=fake-token-123" in url
        assert json == {"url": "https://example.com"}
        return FakeResponse(real_html)

    original_post = browserless_fallback.requests.post
    browserless_fallback.requests.post = fake_post
    try:
        logs = []
        result = browserless_fallback.fetch_rendered_html(
            "https://example.com", api_token="fake-token-123", log=logs.append
        )
        assert result == real_html
        print("PASS: a successful fetch returns the real HTML")
    finally:
        browserless_fallback.requests.post = original_post


def test_non_200_response_returns_none():
    class FakeResponse:
        def __init__(self, text, status_code):
            self.text = text
            self.status_code = status_code

    def fake_post(url, json=None, headers=None, timeout=None):
        return FakeResponse("", 401)

    original_post = browserless_fallback.requests.post
    browserless_fallback.requests.post = fake_post
    try:
        logs = []
        result = browserless_fallback.fetch_rendered_html(
            "https://example.com", api_token="bad-token", log=logs.append
        )
        assert result is None
        assert any("HTTP 401" in l for l in logs)
        print("PASS: a non-200 response (e.g. bad token) returns None, doesn't crash")
    finally:
        browserless_fallback.requests.post = original_post


def test_suspiciously_short_response_treated_as_failure():
    """
    Confirmed real signal per Browserless's own documentation: an
    empty or near-empty response usually means the target site blocked
    the automated browser, not that the page is genuinely this short.
    """
    class FakeResponse:
        def __init__(self, text, status_code=200):
            self.text = text
            self.status_code = status_code

    def fake_post(url, json=None, headers=None, timeout=None):
        return FakeResponse("<html></html>")  # suspiciously short

    original_post = browserless_fallback.requests.post
    browserless_fallback.requests.post = fake_post
    try:
        logs = []
        result = browserless_fallback.fetch_rendered_html(
            "https://example.com", api_token="fake-token", log=logs.append
        )
        assert result is None
        assert any("suspiciously short" in l for l in logs)
        print("PASS: a suspiciously short response is treated as a likely block, not real content")
    finally:
        browserless_fallback.requests.post = original_post


def test_network_error_returns_none_gracefully():
    import requests as requests_module

    def fake_post(url, json=None, headers=None, timeout=None):
        raise requests_module.exceptions.Timeout("connection timed out")

    original_post = browserless_fallback.requests.post
    browserless_fallback.requests.post = fake_post
    try:
        logs = []
        result = browserless_fallback.fetch_rendered_html(
            "https://example.com", api_token="fake-token", log=logs.append
        )
        assert result is None
        assert any("ERROR" in l for l in logs)
        print("PASS: a network error (e.g. timeout) returns None gracefully, doesn't crash")
    finally:
        browserless_fallback.requests.post = original_post


if __name__ == "__main__":
    test_no_token_returns_none_gracefully()
    test_successful_fetch_returns_html()
    test_non_200_response_returns_none()
    test_suspiciously_short_response_treated_as_failure()
    test_network_error_returns_none_gracefully()
    print()
    print("All browserless_fallback.py tests passed.")
