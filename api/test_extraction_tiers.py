"""
Tests for extraction_tiers.py — the tiered listing-field extraction
pipeline built from 9 real, unrelated agency sites inspected in one
session (June 2026). See module docstring for full background.
"""
import sys
sys.path.insert(0, ".")
import extraction_tiers as et


WISEBERRY_HTML = """
<html><body>
<script type="application/ld+json">
[{"@context":"https://schema.org","@type":"Product","name":"16 ADRIAN CLOSE, BATEAU BAY",
"offers":{"@type":"Offer","price":"923000","priceCurrency":"AUD"},
"address":{"@type":"PostalAddress","streetAddress":"16 ADRIAN CLOSE","addressLocality":"BATEAU BAY",
"addressRegion":"NSW","postalCode":"2261","addressCountry":"AU"}}]
</script>
</body></html>
"""

HIGHLAND_HTML = """
<html><head>
<meta property="og:street-address" content="9 Lyle Street">
<meta property="og:locality" content="Ryde">
<meta property="og:region" content="NSW">
<meta property="og:postal-code" content="2112">
<meta name="twitter:title" content="9 Lyle Street, Ryde NSW 2112 - Sold 19/06/2026">
<meta name="twitter:description" content="Sold 19/06/2026 by Highland for Undisclosed.">
</head><body></body></html>
"""

VIRIDITY_SOLD_HTML = """
<html><body>
<h2 class="prop-title pull-left margin0">Sold For $820,000</h2>
<h2 class="prop-title pull-right margin0">76/3 REID AVENUE, WESTMEAD</h2>
</body></html>
"""

BELLE_ACTIVE_HTML = """
<html><body>
<h1 class="address">12 Example Street, Mermaid Waters QLD 4218</h1>
<div class="price">Offers from $795,000</div>
</body></html>
"""

GENERIC_SCAN_HTML = """
<html><body>
<h1>27 Random Street, Nowhere QLD 4000</h1>
<div>$650,000</div>
</body></html>
"""

NOTHING_HTML = "<html><body>just some unrelated page text, no listing data here</body></html>"


def test_tier1_json_ld():
    result = et.try_json_ld(WISEBERRY_HTML)
    assert result is not None
    assert result["tier"] == "json_ld"
    assert result["price"] == "923000"
    assert result["suburb"] == "BATEAU BAY"
    print("PASS: tier 1 (JSON-LD) extracts structured data correctly")


def test_tier2_meta_tags():
    result = et.try_meta_tags(HIGHLAND_HTML)
    assert result is not None
    assert result["tier"] == "meta_tags"
    assert result["suburb"] == "Ryde"
    assert result["status"] == "Sold"
    assert result["price"] == "", "Undisclosed price should not be parsed as a number"
    print("PASS: tier 2 (meta tags) extracts address fields and status from title")


def test_tier3_known_shared_template():
    result = et.try_known_shared_template(VIRIDITY_SOLD_HTML)
    assert result is not None
    assert result["tier"] == "known_template_prop_title"
    assert result["price"] == "820000"
    assert result["status"] == "Sold"
    print("PASS: tier 3 (known shared template) matches the confirmed Viridity/JBRE pattern")


def test_tier3_handles_nested_tags_in_address():
    """
    Regression test for a real bug found via live curl against
    viridityre.com.au/76-3-reid-avenue-westmead-nsw-6194909 (June 2026).
    The address <h2> is NOT flat text — it has a <br> and a nested
    <span> wrapping the actual address. The original regex required no
    tags between the h2's opening tag and its content, which silently
    failed against this real, common page structure.
    """
    real_html = """
    <div class="box-container2" style="background-color:white;color:black;">
      <div class="clearfix padding30">
        <h2 class="prop-title pull-left margin0" style="font-weight:normal!important;">Sold For $820,000 </h2>
        <h2 class="prop-title pull-right margin0">
        <br>				<span style="font-size:0.8em;">76/3  Reid Avenue, Westmead</span>
        </h2>
      </div>
    </div>
    """
    result = et.try_known_shared_template(real_html)
    assert result is not None, "FAIL: should match against real confirmed HTML with nested tags"
    assert result["price"] == "820000"
    assert result["status"] == "Sold"
    assert "76/3" in result["address"] and "Reid Avenue" in result["address"]
    print("PASS: tier 3 correctly handles real nested <br><span> address structure")


def test_tier3b_class_price_address():
    result = et.try_class_price_address(BELLE_ACTIVE_HTML)
    assert result is not None
    assert result["tier"] == "class_price_address"
    assert result["price"] == "795000"
    print("PASS: tier 3b (class=price/address) preserves the original Belle Property finding")


def test_tier3d_reapit_agentbox_pattern():
    """
    Confirmed real pattern (Crystal Realty, June 2026, platform: Reapit/
    Agentbox — confirmed via its own "Powered by Reapit Websites"
    footer): address in <h4>, price as plain text right after, and an
    explicit "Contract" label/value pair giving real status. Fixtures
    built directly from real fetched page content.
    """
    real_sold_html = """
    <html><body>
    <span>Property ID: 1P2799</span>
    <h4>13/54 Regent Street Chippendale NSW</h4>
    <div>$ 890,000</div>
    <div>
    <span>Type</span><span>Apartment</span>
    <span>Contract</span><span>Sold</span>
    <span>Building Size</span><span>94 sqm</span>
    </div>
    </body></html>
    """
    real_active_no_price_html = """
    <html><body>
    <span>Property ID: 1P2789</span>
    <h4>40 Forbes Street Newtown NSW</h4>
    <div>Contact agent</div>
    <div>
    <span>Type</span><span>Land</span>
    <span>Contract</span><span>For Sale</span>
    </div>
    </body></html>
    """

    sold_result = et.try_reapit_agentbox_pattern(real_sold_html)
    assert sold_result is not None
    assert sold_result["address"] == "13/54 Regent Street Chippendale NSW"
    assert sold_result["price"] == "890000"
    assert sold_result["status"] == "Sold"

    active_result = et.try_reapit_agentbox_pattern(real_active_no_price_html)
    assert active_result is not None
    assert active_result["address"] == "40 Forbes Street Newtown NSW"
    assert active_result["price"] == "", "Contact agent listings should have no parseable price"
    assert active_result["status"] == "Active"

    print("PASS: tier 3d (Reapit/Agentbox h4 + Contract field) correctly handles "
          "both sold and Contact-agent active listings")


def test_tier3c_generic_scan():
    result = et.try_generic_dollar_scan(GENERIC_SCAN_HTML)
    assert result is not None
    assert result["tier"] == "generic_dollar_scan"
    assert result["price"] == "650000"
    print("PASS: tier 3c (generic $ scan) works as the lowest-confidence safety net")


def test_priority_order_json_ld_beats_everything():
    """When multiple tiers would match, the earlier (more structured,
    more trustworthy) tier must win."""
    combined = WISEBERRY_HTML + VIRIDITY_SOLD_HTML + BELLE_ACTIVE_HTML
    result = et.extract_listing_fields(combined, "https://example.com/x")
    assert result["tier"] == "json_ld", f"Expected json_ld to win, got {result['tier']}"
    print("PASS: tier priority order is respected when multiple tiers would match")


def test_no_tier_matches_returns_none():
    result = et.extract_listing_fields(NOTHING_HTML, "https://example.com/x")
    assert result is None
    print("PASS: returns None gracefully when no tier finds anything, not a crash")


def test_llm_tier_only_called_when_free_tiers_fail():
    """The LLM tier must never be invoked if a free tier already found
    a usable result — this is the core cost-control guarantee."""
    calls = {"count": 0}

    def fake_post(*args, **kwargs):
        calls["count"] += 1
        raise AssertionError("LLM tier should NOT have been called — a free tier already matched")

    original_post = et.requests.post
    et.requests.post = fake_post
    try:
        # WISEBERRY_HTML matches tier 1, so even with an API key supplied,
        # the LLM tier must never fire.
        result = et.extract_listing_fields(WISEBERRY_HTML, "https://example.com/x", llm_api_key="fake-key")
        assert result["tier"] == "json_ld"
        assert calls["count"] == 0, "LLM tier was called even though a free tier matched"
        print("PASS: LLM tier is never invoked when a free tier already succeeded (cost control)")
    finally:
        et.requests.post = original_post


def test_llm_tier_invoked_only_as_last_resort():
    """Confirms the LLM tier DOES get called when every free tier fails
    and an API key is supplied — the traversgray hidden-input case."""
    class FakeResponse:
        def __init__(self, json_data, status_code=200):
            self._json = json_data
            self.status_code = status_code
        def json(self):
            return self._json

    def fake_post(url, headers=None, json=None, timeout=None):
        return FakeResponse({
            "content": [{"type": "text", "text":
                '{"address": "603/144 Mallett Street, Camperdown", "suburb": "Camperdown", "price": "555000", "status": "Sold"}'}]
        })

    original_post = et.requests.post
    et.requests.post = fake_post
    try:
        result = et.extract_listing_fields(NOTHING_HTML, "https://traversgray.com.au/x", llm_api_key="fake-key")
        assert result is not None
        assert result["tier"] == "llm_extraction"
        assert result["price"] == "555000"
        print("PASS: LLM tier correctly invoked as last resort when every free tier fails")
    finally:
        et.requests.post = original_post


def test_llm_tier_skipped_without_api_key():
    result = et.extract_listing_fields(NOTHING_HTML, "https://example.com/x", llm_api_key=None)
    assert result is None
    print("PASS: LLM tier is skipped entirely (not even attempted) when no API key is supplied")


if __name__ == "__main__":
    test_tier1_json_ld()
    test_tier2_meta_tags()
    test_tier3_known_shared_template()
    test_tier3_handles_nested_tags_in_address()
    test_tier3b_class_price_address()
    test_tier3d_reapit_agentbox_pattern()
    test_tier3c_generic_scan()
    test_priority_order_json_ld_beats_everything()
    test_no_tier_matches_returns_none()
    test_llm_tier_only_called_when_free_tiers_fail()
    test_llm_tier_invoked_only_as_last_resort()
    test_llm_tier_skipped_without_api_key()
    print("\nAll extraction tier tests passed.")
