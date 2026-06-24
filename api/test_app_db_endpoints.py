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
from scraper import Listing

client = app_module.app.test_client()


def test_scrape_office_with_hard_timeout_converts_listing_objects_to_dicts():
    """
    Regression test for THE actual real production crash (confirmed
    via Vercel's function logs, June 24, 2026):
        AttributeError: 'Listing' object has no attribute 'get'
    scrape_office() returns a list of Listing DATACLASS INSTANCES, not
    plain dicts — confirmed by checking scrape_offices() (the existing
    working UI endpoint), which converts via
    [asdict(l) for l in all_listings] before ever returning. Every new
    endpoint added this session (cron-scrape, db.py's
    record_scrape_result) wrongly assumed plain dicts. This crash was
    NOT a timeout issue, despite every symptom (a generic Vercel 500,
    no specific error visible without checking function logs directly)
    looking exactly like the timeout bugs fixed earlier in this same
    session — a real reminder that a generic 500 needs its actual
    traceback checked, not just theorized about from symptoms alone.

    This test also fixes a real gap in EVERY OTHER test in this file:
    they previously used plain dicts to fake listings, which is why
    this bug shipped to production without any test catching it —
    asdict() only works on genuine dataclass instances, so a plain
    dict fixture can never reproduce this exact failure.
    """
    real_listing = Listing(
        address="123 Test St",
        suburb="Newtown",
        guide_price="500000",
        status="active",
        source_adapter="generic_fallback:reapit_agentbox_pattern",
    )

    def fake_scrape_returns_real_listing_objects(domain, *args, **kwargs):
        return ([real_listing], None)

    with patch.object(app_module, "scrape_office", side_effect=fake_scrape_returns_real_listing_objects):
        listings, error = app_module.scrape_office_with_hard_timeout("test.com.au", timeout_seconds=10)
        assert isinstance(listings[0], dict), f"FAIL: should be a plain dict, got {type(listings[0])}"
        assert listings[0]["address"] == "123 Test St"
        # This is the EXACT call that crashed in production — confirm it
        # now works correctly rather than raising AttributeError
        assert listings[0].get("source_adapter") == "generic_fallback:reapit_agentbox_pattern"
    print("PASS: real Listing dataclass instances are correctly converted to plain dicts "
          "(the actual real production crash, not a timeout issue)")


def test_scrape_office_with_hard_timeout_cuts_off_genuinely_slow_sites():
    """
    Regression test for THE real cause of the Guardian Realty crash
    (June 24, 2026): a single office can have many candidate pages to
    visit (e.g. 17 listing pages confirmed real on another site), so
    its TOTAL scrape time can exceed the function's time budget even
    though every individual HTTP request has its own 20s timeout
    (REQUEST_TIMEOUT in scraper.py) — no single request times out, but
    the office as a whole does. The earlier "time budget between
    offices" check couldn't catch this, since it only runs BEFORE
    starting an office, not DURING one. This hard per-office timeout,
    enforced via a background thread, is the real fix.
    """
    import time as time_module

    def slow_scrape(domain, *args, **kwargs):
        time_module.sleep(3)
        return ([Listing(address="should never be returned")], None)

    with patch.object(app_module, "scrape_office", side_effect=slow_scrape):
        start = time_module.monotonic()
        listings, error = app_module.scrape_office_with_hard_timeout("slow.com.au", timeout_seconds=1)
        elapsed = time_module.monotonic() - start
        assert elapsed < 2, f"Should return close to the 1s timeout, took {elapsed:.2f}s"
        assert listings == []
        assert "Timed out after 1s" in error
    print("PASS: a genuinely slow scrape (many pages, no single request times out) is cut off "
          "by the hard per-office timeout, instead of consuming the whole function's time budget")


def test_scrape_office_with_hard_timeout_passes_through_fast_results():
    """A normal, fast scrape should complete and return its real
    result unmodified, well within the timeout."""
    def fast_scrape(domain, *args, **kwargs):
        return ([Listing(address="1 Test St")], None)

    with patch.object(app_module, "scrape_office", side_effect=fast_scrape):
        listings, error = app_module.scrape_office_with_hard_timeout("fast.com.au", timeout_seconds=60)
        assert len(listings) == 1
        assert error is None
    print("PASS: a fast scrape completes normally and returns its real result")


def test_bulk_discover_and_seed_processes_multiple_regions_independently():
    """
    Built to add the rest of QLD's regions efficiently (June 24, 2026),
    replacing the manual one-region-at-a-time UI workflow used for
    Dural and Gold Coast. Each region is processed independently — if
    one region's Places API call fails, the rest still proceed, and
    the response reports per-region results so failures are visible.
    """
    def fake_discover(area, api_key, log=print, max_pages=5):
        if area == "Broken Region":
            raise RuntimeError("Places API quota exceeded")
        if area == "Brisbane CBD QLD":
            return [
                {"name": "Agency A", "place_id": "1", "website": "https://agencya.com.au"},
                {"name": "Agency B", "place_id": "2", "website": None},
            ]
        return [{"name": "Agency C", "place_id": "3", "website": "https://agencyc.com.au"}]

    with patch.object(app_module, "discover_agencies", side_effect=fake_discover), \
         patch.object(app_module.db, "upsert_office", return_value=1):
        resp = client.post("/api/bulk-discover-and-seed", json={
            "regions": ["Brisbane CBD QLD", "Broken Region", "Toowoomba QLD"],
            "apiKey": "fake-key",
        })
        body = resp.get_json()
        assert resp.status_code == 200
        assert len(body["region_results"]) == 3
        brisbane = next(r for r in body["region_results"] if r["region"] == "Brisbane CBD QLD")
        assert brisbane["added"] == 1
        assert brisbane["skipped_no_website"] == 1
        broken = next(r for r in body["region_results"] if r["region"] == "Broken Region")
        assert broken["added"] == 0
        assert "Discovery failed" in broken["error"]
        toowoomba = next(r for r in body["region_results"] if r["region"] == "Toowoomba QLD")
        assert toowoomba["added"] == 1
        assert body["total_offices_added"] == 2
    print("PASS: bulk-discover-and-seed processes multiple regions independently, "
          "one failure doesn't block the others")


def test_bulk_discover_and_seed_validates_inputs():
    resp = client.post("/api/bulk-discover-and-seed", json={"regions": [], "apiKey": "x"})
    assert resp.status_code == 400

    resp = client.post("/api/bulk-discover-and-seed", json={"regions": ["Brisbane"], "apiKey": ""})
    assert resp.status_code == 400
    print("PASS: bulk-discover-and-seed validates empty regions list and missing API key")


def test_bulk_discover_and_seed_db_error_reported_per_region():
    """A DB error during seeding must not crash the whole request — it
    should be reported per-region, with other regions still attempted."""
    def fake_discover(area, api_key, log=print, max_pages=5):
        return [{"name": "Agency A", "place_id": "1", "website": "https://agencya.com.au"}]

    with patch.object(app_module, "discover_agencies", side_effect=fake_discover), \
         patch.object(app_module.db, "upsert_office", side_effect=RuntimeError("connection refused")):
        resp = client.post("/api/bulk-discover-and-seed", json={
            "regions": ["Region One", "Region Two"],
            "apiKey": "fake-key",
        })
        body = resp.get_json()
        assert resp.status_code == 200
        for r in body["region_results"]:
            assert r["added"] == 0
            assert "Database error" in r["error"]
    print("PASS: a DB error during seeding is reported per-region without crashing the whole request")


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
    fake_listing = [Listing(address="1 Test St", source_adapter="generic_fallback:reapit_agentbox_pattern")]
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


def test_cron_scrape_survives_unexpected_exception_from_scrape_office():
    """
    Regression test for a real bug found via live testing (June 24,
    2026): a known-slow site (Guardian Realty) caused an unhandled 500
    that took down OTHER offices in the same cron batch that would
    have scraped fine. scrape_office() can raise an exception we
    haven't seen before, not just return its normal (listings, error)
    tuple — this must be caught per-office, not crash the whole batch.
    """
    fake_offices = [
        {"id": 1, "domain": "broken.com.au", "office_name": "Broken", "region": "Test"},
        {"id": 2, "domain": "fine.com.au", "office_name": "Fine", "region": "Test"},
    ]

    def fake_scrape(domain, *args, **kwargs):
        if domain == "broken.com.au":
            raise ValueError("something genuinely unexpected blew up")
        return ([Listing(address="1 Test St", source_adapter="generic_fallback")], None)

    with patch.object(app_module.db, "get_offices_due_for_scraping", return_value=fake_offices), \
         patch.object(app_module, "scrape_office", side_effect=fake_scrape), \
         patch.object(app_module.db, "record_scrape_result"):
        resp = client.get("/api/cron-scrape?limit=5")
        body = resp.get_json()
        assert resp.status_code == 200, "An unexpected exception in one office must not crash the whole request"
        assert "Unexpected error during scrape" in body["scraped"][0]["error"]
        assert body["scraped"][1]["listing_count"] == 1, "The second, unrelated office must still be processed correctly"
    print("PASS: an unexpected exception from scrape_office() is caught per-office, batch continues")


def test_cron_scrape_stops_gracefully_when_time_budget_exhausted():
    """
    The second real protection added alongside the exception handling
    above: a wall-clock time budget check before each office, so a
    cron run stops gracefully (marking remaining offices for retry
    next time) rather than risk Vercel killing the function mid-
    request, which would look identical to the original crash.
    """
    import time as time_module

    fake_offices = [
        {"id": 1, "domain": "first.com.au", "office_name": "First", "region": "Test"},
        {"id": 2, "domain": "second.com.au", "office_name": "Second", "region": "Test"},
    ]
    call_iter = iter([0, 0, 260, 260])

    def fake_monotonic():
        try:
            return next(call_iter)
        except StopIteration:
            return 260

    with patch.object(app_module.db, "get_offices_due_for_scraping", return_value=fake_offices), \
         patch.object(app_module, "scrape_office", return_value=([], None)), \
         patch.object(app_module.db, "record_scrape_result"), \
         patch.object(app_module.time, "monotonic", side_effect=fake_monotonic):
        resp = client.get("/api/cron-scrape?limit=5")
        body = resp.get_json()
        assert resp.status_code == 200
        assert "time budget exhausted" in body["scraped"][1]["error"]
    print("PASS: the time budget check stops the batch gracefully once exhausted, instead of risking a timeout crash")


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
    test_bulk_discover_and_seed_processes_multiple_regions_independently()
    test_bulk_discover_and_seed_validates_inputs()
    test_bulk_discover_and_seed_db_error_reported_per_region()
    test_scrape_office_with_hard_timeout_converts_listing_objects_to_dicts()
    test_scrape_office_with_hard_timeout_cuts_off_genuinely_slow_sites()
    test_scrape_office_with_hard_timeout_passes_through_fast_results()
    test_seed_offices_adds_and_skips_correctly()
    test_seed_offices_requires_agencies()
    test_seed_offices_database_error_returns_clean_500()
    test_cron_scrape_records_results_with_platform_detected()
    test_cron_scrape_survives_unexpected_exception_from_scrape_office()
    test_cron_scrape_stops_gracefully_when_time_budget_exhausted()
    test_cron_scrape_continues_after_one_office_db_write_fails()
    test_cron_scrape_database_fetch_error_returns_clean_500()
    test_office_status_converts_datetimes_to_strings()
    print()
    print("All app.py database-endpoint tests passed.")
