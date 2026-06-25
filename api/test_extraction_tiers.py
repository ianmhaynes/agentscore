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


def test_tier3d_agentpoint_status_fallback():
    """
    Confirmed real pattern (Park Properties, June 2026, platform:
    Agentpoint — confirmed via its own "Powered by Agentpoint" footer).
    Shares the same <h4>-address + nearby-price shape as Crystal
    Realty's Reapit/Agentbox pattern, but has NO "Contract" label —
    instead a standalone "Sold" text node appears on its own line right
    before the price. Regression test for a real bug found via testing:
    the original fallback code computed price_match.start() relative to
    a substring (after_address) but then sliced the FULL html with that
    offset directly, pointing at completely the wrong region of the
    page and always finding nothing.
    """
    real_sold_html = """
    <html><body>
    <h4>20/12-14 Enmore Road, NEWTOWN</h4>
    <div>Studio</div>
    <h5>Modern studio apartment w/ city views</h5>
    <div>Sold</div>
    <div>$ 490,000</div>
    <div>1</div>
    </body></html>
    """
    result = et.try_reapit_agentbox_pattern(real_sold_html)
    assert result is not None
    assert result["address"] == "20/12-14 Enmore Road, NEWTOWN"
    assert result["price"] == "490000"
    assert result["status"] == "Sold", f"FAIL: {result['status']!r} (offset bug regression)"

    real_active_html = """
    <html><body>
    <h4>5 Test Street, NEWTOWN</h4>
    <div>$ 800,000</div>
    </body></html>
    """
    active_result = et.try_reapit_agentbox_pattern(real_active_html)
    assert active_result["status"] == "", "Listing with no Sold text and no Contract field should not guess a status"

    print("PASS: tier 3d correctly handles the Agentpoint standalone-Sold-text status "
          "fallback (and the offset bug that originally broke it)")


def test_tier3e_semibold_muted_pattern():
    """
    Confirmed real pattern (Pilcher Residential, confirmed via live
    DevTools inspection — June 2026): status in class="semi-bold",
    price in a sibling class="muted" — separate elements, unlike every
    combined-string tier built so far.
    """
    real_html = """
    <html><body>
    <h1>901/32 Maida Street Lilyfield</h1>
    <div class="left flex-40">
    <div class="semi-bold">Sold</div>
    <div><div class="muted">$4,225,000</div></div>
    </div>
    </body></html>
    """
    result = et.try_semibold_muted_pattern(real_html)
    assert result is not None
    assert result["price"] == "4225000"
    assert result["status"] == "Sold"
    assert "Maida Street" in result["address"]
    print("PASS: tier 3e (semi-bold/muted) correctly parses Pilcher's confirmed structure")


def test_tier3f_renet_hidden_input_pattern():
    """
    Confirmed real pattern (Travers Gray Real Estate, platform: ReNet,
    confirmed via "Marketing by ... and ReNet Real Estate Software"
    footer). CORRECTED June 23, 2026 — an earlier version of this tier
    assumed a heading-pair structure (<h2>{suburb}</h2><h3>{street}</h3>)
    based on a web_fetch markdown conversion that turned out to
    misrepresent the real page. Direct curl against a live listing
    (https://traversgray.com.au/21631808) confirmed NO such heading
    exists anywhere in the raw HTML — the address was only ever present
    as a combined string inside hidden form inputs:
        <input type="hidden" name="extra_data[address]" value="603/144 Mallett Street, Camperdown" />
        <input type="hidden" name="extra_data[price]" value="Sold for $555,000" />
    This is the SAME hidden-input pattern first found on traversgray
    months ago at the very start of this project — the heading-based
    tier built earlier today was solving an already-solved problem with
    a less reliable approach, based on an unverified assumption about
    page structure. This test fixture is built directly from real grep
    output against the live site, not a guess.
    """
    real_sold_html = """
    <input type="hidden" name="extra_data[address]" id="address" value="603/144 Mallett Street, Camperdown" />
    <input type="hidden" name="extra_data[heading]" id="heading" value="UNDER OFFER!" />
    <input type="hidden" name="extra_data[price]" id="price" value="Sold for $555,000" />
    """
    result = et.try_renet_hidden_input_pattern(real_sold_html)
    assert result is not None
    assert result["address"] == "603/144 Mallett Street, Camperdown"
    assert result["suburb"] == "Camperdown"
    assert result["price"] == "555000"
    assert result["status"] == "Sold"

    real_active_html = """
    <input type="hidden" name="extra_data[address]" id="address" value="12 Test Street, Erskineville" />
    <input type="hidden" name="extra_data[price]" id="price" value="For Sale $899,000" />
    """
    active_result = et.try_renet_hidden_input_pattern(real_active_html)
    assert active_result["status"] == "Active"
    assert active_result["price"] == "899000"

    print("PASS: tier 3f (ReNet hidden-input pattern) correctly parses both sold and active "
          "Travers Gray listings — built from real confirmed raw HTML, not a markdown-derived guess")


def test_tier3d_contract_field_with_whitespace_between_tags():
    """
    Regression test for a real, subtle bug found via a direct curl
    check against the LIVE Crystal Realty site (June 2026) — not
    findable via any hand-written fixture, since every fixture in this
    file happened to place tags with no whitespace between them. The
    real production HTML has a newline between </label> and the
    following <div>:
        <label class="detail-label">Contract</label>
        <div class="detail-value">Sold</div>
    The original regex's tag-skipping group only matched directly-
    adjacent tags with zero whitespace, so it silently stopped at
    </label> and never reached the actual "Sold" value — every single
    "Contract" field on the real site failed to match despite the
    exact same logic working in every prior test fixture. This is why
    the live deployed app returned status="Active" for confirmed-sold
    listings even after the tier itself was "tested and passing."
    """
    real_html_exact_from_curl = (
        '<div class="detail-value">Terrace</div>\n'
        '</li>\n'
        '<li>\n'
        '<label class="detail-label">Contract</label>\n'
        '<div class="detail-value">Sold</div>\n'
        '</li>\n'
    )
    full_page = f"""
    <html><body>
    <h4>2B Hopetoun Street Petersham NSW</h4>
    <div>$ 1,670,000</div>
    <ul><li>
    <label class="detail-label">Type</label>
    {real_html_exact_from_curl}
    </li></ul>
    </body></html>
    """
    result = et.try_reapit_agentbox_pattern(full_page)
    assert result is not None
    assert result["status"] == "Sold", (
        f"FAIL: {result['status']!r} — this is the exact real bug that shipped to "
        f"production despite passing every prior test"
    )
    assert result["price"] == "1670000"
    print("PASS: Contract field correctly matched even with whitespace/newlines between "
          "tags (the exact real bug found via live curl, invisible to every prior fixture)")


def test_tier3h_wordpress_epl_pattern():
    """
    Confirmed real pattern (Woolloongabba Real Estate, WordPress "EPL"
    real estate plugin, confirmed via "?action=epl_search&post_type=
    property" query params on the site's own nav links — June 24,
    2026): address combined in a plain <h1> (full street + suburb +
    state + postcode, multiple spaces between street and suburb), price
    as plain text nearby, no combined "Sold For $X" text on active
    listings. Built GENERICALLY (any h1 + nearby $ amount) since only
    markdown-converted content was available to confirm this structure,
    not verified raw HTML.
    """
    real_active_html = """
    <html><body>
    <h1>47 Shore Street   Russell Island QLD 4184</h1>
    <div>3 Bed</div>
    $475,000
    </body></html>
    """
    result = et.try_wordpress_epl_pattern(real_active_html)
    assert result is not None
    assert result["price"] == "475000"
    assert result["suburb"] == "Russell Island"
    assert result["status"] == ""

    real_sold_html = """
    <html><body>
    <h1>506/19 Hope Street   South Brisbane QLD 4101</h1>
    Sold
    $650,000
    </body></html>
    """
    sold_result = et.try_wordpress_epl_pattern(real_sold_html)
    assert sold_result["status"] == "Sold"
    assert sold_result["price"] == "650000"
    print("PASS: tier 3h (WordPress EPL pattern) correctly parses both active and sold listings")


def test_tier3g_eagle_software_no_longer_collides_with_other_h1_based_tiers():
    """
    Regression test for a REAL BUG found while building tier 3h
    (June 24, 2026): try_eagle_software_pattern() originally matched
    ANY page with an <h1>, even with no <h2> price found at all,
    silently returning an empty price. This made it incorrectly
    "win" the match on a DIFFERENT site (Woolloongabba Real Estate,
    WordPress EPL plugin) that also uses a plain <h1> for the address
    but puts its price as nearby plain text, not inside an <h2> —
    since Eagle Software's tier ran first in the pipeline, it stole
    the match with wrong/empty data instead of letting the correct
    tier (3h) handle it. Fixed by requiring the actual <h2> price
    element — Eagle Software's real, distinguishing signature — to be
    found before claiming a match at all.
    """
    epl_style_html = """
    <html><body>
    <h1>47 Shore Street   Russell Island QLD 4184</h1>
    <div>3 Bed</div>
    $475,000
    </body></html>
    """
    result = et.try_eagle_software_pattern(epl_style_html)
    assert result is None, (
        f"FAIL: Eagle Software should step aside when there's no real <h2> price, "
        f"but it matched: {result}"
    )

    # Confirm Eagle Software's OWN real confirmed structure still works
    eagle_html = """
    <html><body>
    <h1>2 Chisholm Avenue, Clemton Park</h1>
    <h2>$1,827,000</h2>
    </body></html>
    """
    eagle_result = et.try_eagle_software_pattern(eagle_html)
    assert eagle_result is not None
    assert eagle_result["price"] == "1827000"

    # Confirm the full pipeline now correctly routes to tier 3h, not 3g, for the EPL-style page
    full_result = et.extract_listing_fields(
        epl_style_html,
        "https://woolloongabbarealestate.com.au/properties-for-sale/47-shore-street-russell-island-qld-4184/",
    )
    assert full_result["tier"] == "wordpress_epl_pattern", (
        f"FAIL: full pipeline should route to tier 3h, got {full_result['tier']!r}"
    )
    print("PASS: Eagle Software no longer collides with other h1-based tiers — "
          "requires its own real <h2> price signature to match, own real case still works")


def test_tier3i_rex_websites_pattern():
    """
    Confirmed real pattern (Kangaroo Point Real Estate, platform: Rex
    Websites — confirmed via "Powered by Rex Websites" footer credit,
    June 24, 2026): SOLD and price appear BEFORE a plain <h1> address
    (the opposite ordering from most other h1-based tiers, where price
    comes after). Distinct from BresicWhitney's "Rex CRM" (a confirmed
    JS-gated dead end from an earlier session) — "Rex Websites" is a
    different, fully server-rendered product from the same company.
    """
    real_html = """
    <html><body>
    SOLD
    $1,010,000
    <h1>8 / 50 Rotherham Street, Kangaroo Point QLD 4169</h1>
    <div>1 Bed</div>
    </body></html>
    """
    result = et.try_rex_websites_pattern(real_html)
    assert result is not None
    assert result["price"] == "1010000"
    assert result["status"] == "Sold"
    assert result["suburb"] == "Kangaroo Point"
    print("PASS: tier 3i (Rex Websites) correctly parses real confirmed structure")


def test_tier3h_wordpress_epl_no_longer_collides_with_rex_websites():
    """
    Regression test for a REAL BUG found while building tier 3i
    (June 24, 2026): try_wordpress_epl_pattern() originally matched
    ANY page with an h1, even with no real price found AFTER it —
    which caused it to collide with the new Rex Websites tier, since
    that platform puts its price BEFORE the h1, leaving no real $
    amount in the text immediately following. Fixed using the same
    proven pattern as the earlier Eagle Software collision fix: require
    the tier's own real distinguishing signature (a price genuinely
    found after the address) before claiming a match.
    """
    rex_style_html = """
    <html><body>
    SOLD
    $1,010,000
    <h1>8 / 50 Rotherham Street, Kangaroo Point QLD 4169</h1>
    <div>1 Bed</div>
    </body></html>
    """
    result = et.try_wordpress_epl_pattern(rex_style_html)
    assert result is None, (
        f"FAIL: WordPress EPL tier should step aside when there's no real price after the h1, "
        f"but it matched: {result}"
    )

    # Confirm WordPress EPL's own real case still works
    woolloongabba_html = """
    <html><body>
    <h1>47 Shore Street   Russell Island QLD 4184</h1>
    <div>3 Bed</div>
    $475,000
    </body></html>
    """
    own_result = et.try_wordpress_epl_pattern(woolloongabba_html)
    assert own_result is not None
    assert own_result["price"] == "475000"

    # Confirm the full pipeline correctly routes the Rex Websites page to tier 3i, not 3h
    full_result = et.extract_listing_fields(
        rex_style_html,
        "https://kangaroopointrealestate.com.au/listings/residential_sale-R2-5091526-kangaroo-point",
    )
    assert full_result["tier"] == "rex_websites_pattern", (
        f"FAIL: full pipeline should route to tier 3i, got {full_result['tier']!r}"
    )
    print("PASS: WordPress EPL no longer collides with Rex Websites pattern — "
          "requires its own real price-after-address signature, own real case still works")


def test_tier3g_eagle_software_pattern():
    """
    Confirmed real pattern (Living Estate Agents, platform: Eagle
    Software, confirmed via "Powered by Eagle Software" footer —
    June 23, 2026): address combined in one <h1>, price alone in the
    next <h2>. Confirmed real limitation: the DETAIL page itself never
    shows "Sold"/"For Sale" text — that only appears on the recently-
    sold INDEX page's card — so this tier intentionally returns an
    empty status rather than guessing, and the listing URL itself
    (a /property?property_id=... query string) never contains a
    /sold/-style path segment either, so the caller's URL-path status
    fallback also can't help here. Real, honest gap: every livingea
    listing currently defaults to "Active" even when genuinely sold.
    """
    real_html = """
    <html><body>
    <h1>2 Chisholm Avenue, Clemton Park</h1>
    <h2>$1,827,000</h2>
    </body></html>
    """
    result = et.try_eagle_software_pattern(real_html)
    assert result is not None
    assert result["address"] == "2 Chisholm Avenue, Clemton Park"
    assert result["suburb"] == "Clemton Park"
    assert result["price"] == "1827000"
    assert result["status"] == "", "Status should be intentionally empty, not guessed"
    print("PASS: tier 3g (Eagle Software h1/h2) correctly extracts address+price, "
          "intentionally leaves status for the caller (a real, documented limitation)")


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
    test_tier3d_agentpoint_status_fallback()
    test_tier3e_semibold_muted_pattern()
    test_tier3f_renet_hidden_input_pattern()
    test_tier3g_eagle_software_pattern()
    test_tier3h_wordpress_epl_pattern()
    test_tier3g_eagle_software_no_longer_collides_with_other_h1_based_tiers()
    test_tier3i_rex_websites_pattern()
    test_tier3h_wordpress_epl_no_longer_collides_with_rex_websites()
    test_tier3d_contract_field_with_whitespace_between_tags()
    test_tier3c_generic_scan()
    test_priority_order_json_ld_beats_everything()
    test_no_tier_matches_returns_none()
    test_llm_tier_only_called_when_free_tiers_fail()
    test_llm_tier_invoked_only_as_last_resort()
    test_llm_tier_skipped_without_api_key()
    print("\nAll extraction tier tests passed.")
