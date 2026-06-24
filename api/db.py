"""
db.py — Database connectivity for AgentScore.

Reads connection details from the DATABASE_URL environment variable
(set in Vercel's dashboard, pointing at the Supabase Postgres instance
created June 24, 2026). All functions here are intentionally simple,
synchronous, and use plain psycopg2 — no ORM — to keep this easy to
read and debug, matching the rest of the project's style.

Connections are opened and closed per-call rather than pooled in
Python, since Supabase's connection string (port 6543) is ALREADY a
pooled connection via PgBouncer — adding a second pooling layer on top
in Python would be redundant and could cause its own problems in a
serverless environment where each function invocation may be a fresh
process anyway.
"""
import os
import psycopg2
import psycopg2.extras


def get_connection():
    """
    Returns a new psycopg2 connection using DATABASE_URL.
    Raises a clear error if the environment variable is missing,
    rather than a cryptic psycopg2 error, since a missing env var is
    the most likely real-world failure mode (e.g. forgot to redeploy
    after adding it, or a typo in the variable name).
    """
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Check Vercel project settings -> Environment Variables."
        )
    return psycopg2.connect(database_url)


def upsert_office(domain, office_name=None, region=None):
    """
    Inserts a new office row, or does nothing if the domain already
    exists (ON CONFLICT DO NOTHING) — offices are meant to be added
    once via discovery, then re-scraped repeatedly, not re-inserted.
    Returns the office's id either way (existing or newly created),
    since callers need this to write listing_snapshots against it.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO offices (domain, office_name, region)
                VALUES (%s, %s, %s)
                ON CONFLICT (domain) DO UPDATE SET
                    office_name = COALESCE(EXCLUDED.office_name, offices.office_name),
                    region = COALESCE(EXCLUDED.region, offices.region)
                RETURNING id
                """,
                (domain, office_name, region),
            )
            office_id = cur.fetchone()[0]
        conn.commit()
        return office_id
    finally:
        conn.close()


def get_offices_due_for_scraping(limit=10):
    """
    Returns offices that either have never been scraped, or were last
    scraped more than 20 hours ago (a bit under a day, so a daily cron
    job doesn't accidentally skip a day due to timing drift). Ordered
    by last_scraped_at ascending (oldest/never-scraped first), so a
    limited daily batch makes steady progress through the whole list
    rather than always hitting the same offices.
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, domain, office_name, region
                FROM offices
                WHERE last_scraped_at IS NULL
                   OR last_scraped_at < now() - INTERVAL '20 hours'
                ORDER BY last_scraped_at ASC NULLS FIRST
                LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def record_scrape_result(office_id, listings, platform_detected=None, error=None):
    """
    Writes one row to listing_snapshots per listing found, and updates
    the office's last_scraped_at / last_success_at / last_error fields.
    last_success_at is only updated when there's no error, so a
    transient failure doesn't erase the record of when this office
    last genuinely produced data — useful for distinguishing "newly
    added, not yet scraped" from "was working, now failing" later.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if error:
                cur.execute(
                    """
                    UPDATE offices
                    SET last_scraped_at = now(),
                        last_error = %s,
                        platform_detected = COALESCE(%s, platform_detected)
                    WHERE id = %s
                    """,
                    (error, platform_detected, office_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE offices
                    SET last_scraped_at = now(),
                        last_success_at = now(),
                        last_error = NULL,
                        platform_detected = COALESCE(%s, platform_detected)
                    WHERE id = %s
                    """,
                    (platform_detected, office_id),
                )

            for listing in listings:
                cur.execute(
                    """
                    INSERT INTO listing_snapshots (
                        office_id, listing_url, address, suburb, postcode,
                        guide_price, sold_price, status, agent_name,
                        agent_email, agent_phone, source_adapter,
                        extraction_confidence
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        office_id,
                        listing.get("listing_url", ""),
                        listing.get("address", ""),
                        listing.get("suburb", ""),
                        listing.get("postcode", ""),
                        listing.get("guide_price", ""),
                        listing.get("sold_price", ""),
                        listing.get("status", ""),
                        listing.get("agent_name", ""),
                        listing.get("agent_email", ""),
                        listing.get("agent_phone", ""),
                        listing.get("source_adapter", ""),
                        listing.get("extraction_confidence", ""),
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def get_office_status_summary():
    """
    Returns a simple per-office summary for a monitoring view: domain,
    when it was last scraped, whether the last attempt succeeded, and
    how many listing snapshots it has produced in total. This is the
    "which offices are working, which are failing, and why" view from
    Day 3 of the production plan.
    """
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    o.id,
                    o.domain,
                    o.office_name,
                    o.region,
                    o.platform_detected,
                    o.last_scraped_at,
                    o.last_success_at,
                    o.last_error,
                    COUNT(ls.id) AS total_snapshots
                FROM offices o
                LEFT JOIN listing_snapshots ls ON ls.office_id = o.id
                GROUP BY o.id
                ORDER BY o.last_scraped_at DESC NULLS FIRST
                """
            )
            return cur.fetchall()
    finally:
        conn.close()
