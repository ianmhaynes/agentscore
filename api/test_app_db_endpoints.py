"""
Tests for the new database-backed endpoints added to app.py on
June 24, 2026: /api/seed-offices, /api/cron-scrape, /api/office-status.
All database calls are mocked so these tests run without needing a
real DATABASE_URL — the existing /api/db-test endpoint (also added
this session) is how the REAL connection gets verified, against the
live Vercel deployment, the same "real bytes, not a mock" principle
used throughout this project.
"""
import sys
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, ".")
import app as app_module

client = app_module.app.test_client()


def test_seed_offices_adds_and_skips_correctly():
    with patch.object(app_module.db, "upsert_office", return_value=42) as mock_upsert:
        resp = client.post("/api/seed-offices", json={
            "agencies": [
                {"name": "Test Realty", "website": "https://testrealty.com.au", "place_id": "abc"},
                {"name": "No Website Agency", "website": None, "place_id": "def"},
            ],
            "region": "Newtown NSW 2042",
        })
        body = resp.get_json()
        assert resp.status_code == 200
        assert len(body["added"]) == 1
        assert body["added"][0]["domain"] == "testrealty.com.au"
        assert len(body["skipped_no_website"]) == 1
        assert mock_upsert.called
    print("PASS: seed-offices adds offices with websites, skips those without")


def test_seed_offices_requires_agencies():
    resp = client.post("/api/seed-offices", json={"agencies": []})
    assert resp.status_code == 400
    print("PASS: seed-offices rejects an empty agencies list with 400")


def test_seed_offices_database_error_returns_clean_500():
    with patch.object(app_module.db, "upsert_office", side_effect=RuntimeError("connection refused")):
        resp = client.post("/api/seed-offices", json={
            "agencies": [{"name": "Test Realty", "website": "https://testrealty.com.au"}],
        })
        body = resp.get_json()
        assert resp.status_code == 500
        assert "Database error" in body["error"]
    print("PASS: a database error in seed-offices returns a clean JSON 500, not an unhandled crash")


def test_cron_scrape_records_results_with_platform_detected():
    fake_offices = [{"id": 1, "domain": "example.com.au", "office_name": "Example", "region": "Test"}]
    fake_listing = [{"address": "1 Test St", "source_adapter": "generic_fallback:reapit_agentbox_pattern"}]
    with patch.object(app_module.db, "get_offices_due_for_scraping", return_value=fake_offices), \
         patch.object(app_module, "scrape_office", return_value=(fake_listing, None)) as mock_scrape, \
         patch.object(app_module.db, "record_scrape_result") as mock_record:
        resp = client.get("/api/cron-scrape?limit=5")
        body = resp.get_json()
        assert resp.status_code == 200
        assert body["scraped"][0]["domain"] == "example.com.au"
        assert body["scraped"][0]["listing_count"] == 1
        assert mock_scrape.called
        assert mock_record.called
        assert mock_record.call_args[1]["platform_detected"] == "generic_fallback:reapit_agentbox_pattern"
    print("PASS: cron-scrape scrapes due offices and records results with the detected platform")


def test_cron_scrape_continues_after_one_office_db_write_fails():
    """A DB write failure for ONE office must not abort the rest of the
    batch — each office is independent, and the next cron run will
    simply retry whichever one failed (its last_scraped_at is unchanged)."""
    fake_offices = [
        {"id": 1, "domain": "good.com.au", "office_name": "Good", "region": "Test"},
        {"id": 2, "domain": "bad.com.au", "office_name": "Bad", "region": "Test"},
    ]
    with patch.object(app_module.db, "get_offices_due_for_scraping", return_value=fake_offices), \
         patch.object(app_module, "scrape_office", return_value=([], None)), \
         patch.object(app_module.db, "record_scrape_result", side_effect=[None, RuntimeError("write failed")]):
        resp = client.get("/api/cron-scrape?limit=5")
        body = resp.get_json()
        assert resp.status_code == 200
        assert len(body["scraped"]) == 2, "Both offices should appear in the response, even though one failed"
        assert "DB write failed" in body["scraped"][1]["error"]
    print("PASS: a DB write failure for one office doesn't abort the rest of the cron batch")


def test_cron_scrape_database_fetch_error_returns_clean_500():
    with patch.object(app_module.db, "get_offices_due_for_scraping", side_effect=RuntimeError("db down")):
        resp = client.get("/api/cron-scrape")
        body = resp.get_json()
        assert resp.status_code == 500
        assert "Database error" in body["error"]
    print("PASS: a failure fetching due offices returns a clean JSON 500")


def test_office_status_converts_datetimes_to_strings():
    fake_summary = [{
        "id": 1, "domain": "example.com.au",
        "last_scraped_at": datetime(2026, 6, 24, 10, 0),
        "last_success_at": datetime(2026, 6, 24, 10, 0),
        "last_error": None, "total_snapshots": 5,
    }]
    with patch.object(app_module.db, "get_office_status_summary", return_value=fake_summary):
        resp = client.get("/api/office-status")
        body = resp.get_json()
        assert resp.status_code == 200
        assert isinstance(body["offices"][0]["last_scraped_at"], str)
    print("PASS: office-status converts datetime objects to JSON-safe ISO strings")


if __name__ == "__main__":
    test_seed_offices_adds_and_skips_correctly()
    test_seed_offices_requires_agencies()
    test_seed_offices_database_error_returns_clean_500()
    test_cron_scrape_records_results_with_platform_detected()
    test_cron_scrape_continues_after_one_office_db_write_fails()
    test_cron_scrape_database_fetch_error_returns_clean_500()
    test_office_status_converts_datetimes_to_strings()
    print()
    print("All app.py database-endpoint tests passed.")
