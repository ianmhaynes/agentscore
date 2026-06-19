"""Tests for scoring.py — agent variance scoring."""
import sys
sys.path.insert(0, ".")
from scoring import score_agents, summary_stats


def _listing(status, agent, office, guide, sold, confidence="high", source="ray_white_dynamics"):
    return {
        "status": status, "agent_name": agent, "office_name": office,
        "guide_price": guide, "sold_price": sold,
        "extraction_confidence": confidence, "source_adapter": source,
        "address": "1 Test St",
    }


def test_minimum_sales_threshold():
    listings = [
        _listing("Sold", "Agent A", "Office 1", "700000", "710000"),
        _listing("Sold", "Agent A", "Office 1", "700000", "710000"),
    ]
    scores = score_agents(listings)
    assert len(scores) == 0, "Agent with only 2 priced sales should not be ranked"
    print("PASS: minimum sales threshold (3) correctly excludes under-threshold agents")


def test_missing_price_excluded_from_scoring():
    listings = [
        _listing("Sold", "Agent A", "Office 1", "700000", "710000"),
        _listing("Sold", "Agent A", "Office 1", "700000", "710000"),
        _listing("Sold", "Agent A", "Office 1", "", "710000"),  # missing guide price
    ]
    scores = score_agents(listings)
    assert len(scores) == 0, "Should still be excluded — only 2 of 3 sales have both prices"
    print("PASS: rows missing guide/sold price correctly excluded from scored_sales count")


def test_active_listings_never_scored():
    listings = [
        _listing("Active", "Agent A", "Office 1", "700000", ""),
        _listing("Active", "Agent A", "Office 1", "700000", ""),
        _listing("Active", "Agent A", "Office 1", "700000", ""),
    ]
    scores = score_agents(listings)
    assert len(scores) == 0, "Active listings should never count toward scoring"
    print("PASS: active listings correctly excluded from scoring entirely")


def test_co_listed_agents_each_get_full_credit():
    listings = []
    for guide, sold in [("900000", "920000"), ("400000", "410000"), ("300000", "305000")]:
        listings.append(_listing("Sold", "Agent E", "Office 1", guide, sold))
        listings.append(_listing("Sold", "Agent F", "Office 1", guide, sold))
    scores = score_agents(listings)
    e = next(s for s in scores if s.agent_name == "Agent E")
    f = next(s for s in scores if s.agent_name == "Agent F")
    assert e.total_sales == 3 and f.total_sales == 3
    assert e.scored_sales == 3 and f.scored_sales == 3
    print("PASS: co-listed agents each receive full credit for shared sales")


def test_score_bands():
    # Band A: <= 3% variance
    listings_a = [_listing("Sold", "Agent A", "O", "1000000", "1010000")] * 3
    # Band B: 3-7% variance
    listings_b = [_listing("Sold", "Agent B", "O", "1000000", "1050000")] * 3
    # Band C: > 7% variance
    listings_c = [_listing("Sold", "Agent C", "O", "1000000", "1200000")] * 3

    scores_a = score_agents(listings_a)
    scores_b = score_agents(listings_b)
    scores_c = score_agents(listings_c)

    assert scores_a[0].score_band == "A", f"FAIL: {scores_a[0].score_band}"
    assert scores_b[0].score_band == "B", f"FAIL: {scores_b[0].score_band}"
    assert scores_c[0].score_band == "C", f"FAIL: {scores_c[0].score_band}"
    print("PASS: A/B/C score bands correctly assigned based on variance magnitude")


def test_confidence_mixed_detection():
    listings = [
        _listing("Sold", "Agent G", "O", "500000", "510000", confidence="high"),
        _listing("Sold", "Agent G", "O", "500000", "510000", confidence="medium"),
        _listing("Sold", "Agent G", "O", "500000", "510000", confidence="high"),
    ]
    scores = score_agents(listings)
    assert scores[0].confidence == "mixed", f"FAIL: {scores[0].confidence}"

    listings_uniform = [
        _listing("Sold", "Agent H", "O", "500000", "510000", confidence="high"),
        _listing("Sold", "Agent H", "O", "500000", "510000", confidence="high"),
        _listing("Sold", "Agent H", "O", "500000", "510000", confidence="high"),
    ]
    scores2 = score_agents(listings_uniform)
    assert scores2[0].confidence == "high", f"FAIL: {scores2[0].confidence}"
    print("PASS: confidence correctly reported as 'mixed' or the uniform value")


def test_negative_variance_handled():
    """Underquoting (sold well above guide) and overquoting (sold below
    guide) should both compute correctly, not just positive variance."""
    listings = [_listing("Sold", "Agent A", "O", "1000000", "950000")] * 3
    scores = score_agents(listings)
    assert scores[0].avg_variance_pct < 0, "Sold below guide should be negative variance"
    assert abs(scores[0].avg_variance_pct - (-0.05)) < 0.0001
    print("PASS: negative variance (sold below guide) computed correctly")


def test_summary_stats_basic():
    listings = [
        _listing("Sold", "Agent A", "Office 1", "1000000", "1010000"),
        _listing("Sold", "Agent A", "Office 1", "1000000", "1010000"),
        _listing("Sold", "Agent A", "Office 1", "1000000", "1010000"),
        _listing("Active", "Agent A", "Office 1", "999999", ""),
    ]
    scores = score_agents(listings)
    stats = summary_stats(listings, scores)
    assert stats["Total listing rows"] == 4
    assert "Office 1" in stats["Offices included"]
    assert stats["Agents ranked (min 3 priced sales)"] == 1
    print("PASS: summary_stats produces sensible aggregate figures")


if __name__ == "__main__":
    test_minimum_sales_threshold()
    test_missing_price_excluded_from_scoring()
    test_active_listings_never_scored()
    test_co_listed_agents_each_get_full_credit()
    test_score_bands()
    test_confidence_mixed_detection()
    test_negative_variance_handled()
    test_summary_stats_basic()
    print("\nAll scoring tests passed.")
