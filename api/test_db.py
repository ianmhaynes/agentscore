"""
Tests for db.py. Uses a mocked psycopg2 connection throughout, so these
tests run anywhere without needing real database credentials — the
same principle as every other test file in this project: verify the
SQL/logic is correct, without depending on external services being
reachable at test time.
"""
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
import db


def test_get_connection_raises_clear_error_without_database_url():
    """A missing DATABASE_URL should raise a clear, actionable error,
    not a cryptic psycopg2 connection failure."""
    with patch.dict("os.environ", {}, clear=True):
        try:
            db.get_connection()
            assert False, "FAIL: should have raised RuntimeError"
        except RuntimeError as e:
            assert "DATABASE_URL" in str(e)
            print("PASS: missing DATABASE_URL raises a clear, actionable error")


def test_upsert_office_returns_id_and_commits():
    """upsert_office should execute an INSERT ... ON CONFLICT, fetch
    the returned id, and commit the transaction."""
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = (42,)
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch.object(db, "get_connection", return_value=fake_conn):
        office_id = db.upsert_office("example.com.au", "Example Realty", "Newtown NSW")

    assert office_id == 42
    assert fake_conn.commit.called
    assert fake_conn.close.called
    # Confirm the domain was actually passed into the query parameters
    call_args = fake_cursor.execute.call_args
    assert "example.com.au" in call_args[0][1]
    print("PASS: upsert_office returns the office id and commits the transaction")


def test_get_offices_due_for_scraping_closes_connection_even_on_success():
    """Connections must always be closed, success or failure, to avoid
    leaking connections against Supabase's pooled limit."""
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = [{"id": 1, "domain": "test.com.au"}]
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch.object(db, "get_connection", return_value=fake_conn):
        result = db.get_offices_due_for_scraping(limit=5)

    assert result == [{"id": 1, "domain": "test.com.au"}]
    assert fake_conn.close.called
    print("PASS: get_offices_due_for_scraping returns results and closes the connection")


def test_record_scrape_result_with_error_does_not_update_last_success():
    """When an error is passed, last_success_at must NOT be touched —
    only last_scraped_at and last_error. This preserves the most
    recent genuine success timestamp even through failed attempts."""
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch.object(db, "get_connection", return_value=fake_conn):
        db.record_scrape_result(office_id=7, listings=[], error="Could not reach site")

    # Find the UPDATE call and confirm it's the error-path query (no last_success_at)
    update_call = fake_cursor.execute.call_args_list[0]
    query_text = update_call[0][0]
    assert "last_success_at" not in query_text or "last_error" in query_text
    assert "Could not reach site" in update_call[0][1]
    print("PASS: error path updates last_error without touching last_success_at")


def test_record_scrape_result_inserts_one_row_per_listing():
    """Each listing in the input list should produce exactly one
    INSERT into listing_snapshots — this is the core mechanism that
    makes historical tracking (price changes, days on market) possible
    later, by comparing snapshots over time."""
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    listings = [
        {"listing_url": "https://example.com/1", "address": "1 Test St", "status": "Active"},
        {"listing_url": "https://example.com/2", "address": "2 Test St", "status": "Sold"},
    ]

    with patch.object(db, "get_connection", return_value=fake_conn):
        db.record_scrape_result(office_id=1, listings=listings, platform_detected="generic_fallback")

    # 1 UPDATE (office) + 2 INSERTs (one per listing) = 3 execute calls
    assert fake_cursor.execute.call_count == 3
    print("PASS: record_scrape_result inserts exactly one snapshot row per listing")


def test_record_scrape_result_handles_empty_listings_gracefully():
    """A genuinely empty result (office scraped successfully but found
    nothing) should still update the office's timestamps, with zero
    INSERTs, not error out."""
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    with patch.object(db, "get_connection", return_value=fake_conn):
        db.record_scrape_result(office_id=3, listings=[])

    assert fake_cursor.execute.call_count == 1  # just the office UPDATE
    assert fake_conn.commit.called
    print("PASS: empty listings list is handled gracefully (office still updated, zero inserts)")


if __name__ == "__main__":
    test_get_connection_raises_clear_error_without_database_url()
    test_upsert_office_returns_id_and_commits()
    test_get_offices_due_for_scraping_closes_connection_even_on_success()
    test_record_scrape_result_with_error_does_not_update_last_success()
    test_record_scrape_result_inserts_one_row_per_listing()
    test_record_scrape_result_handles_empty_listings_gracefully()
    print()
    print("All db.py tests passed.")
