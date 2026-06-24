"""
browserless_fallback.py — Optional headless-browser rendering, used
ONLY as a last resort when a site's listing data isn't reachable via
plain HTTP at all (confirmed JS-gated platforms, e.g. LJ Hooker's
HubSpot generation, BresicWhitney's Rex CRM delivery layer).

WHY THIS EXISTS: two genuine attempts at evaluating Playwright earlier
in this project were both declined for cost reasons, reasoning that
ran ANY scrape through a real browser by default. Confirmed via real
testing across ~90 sites (June 2026) that genuinely JS-gated platforms
are a small minority (~2%) — used ONLY as a fallback, not a default,
the real cost is small enough to fit comfortably inside Browserless's
free tier (1,000 units/month) even at a meaningfully larger scale.

HOW IT WORKS: Browserless runs the actual headless browser on its own
infrastructure — Vercel's serverless Python functions cannot run a
real Chrome binary locally, so this module makes a plain HTTP POST to
Browserless's REST API (the /content endpoint specifically), which
returns fully JS-rendered HTML as a plain string. That HTML is then
fed through the EXACT SAME extraction_tiers.py pipeline used
everywhere else in this project — no separate parsing logic needed,
since once JavaScript has run, the result is still just HTML.

REQUIRES a Browserless API token, supplied by the caller per-request
(same pattern as the Google Places and Anthropic API keys elsewhere in
this project) — never hardcoded, never stored server-side.

NOT YET CONFIRMED LIVE — this exact integration has not been run
against the real Browserless API with a real token. The request shape
is confirmed correct from Browserless's own current documentation
(June 2026), but the full pipeline (fetch -> extract) has only been
verified with mocked responses, not end-to-end against the live API.
"""
import requests

REQUEST_TIMEOUT = 45  # browser rendering genuinely takes longer than a plain HTTP request
BROWSERLESS_CONTENT_URL = "https://production-sfo.browserless.io/content"


def fetch_rendered_html(url, api_token, log=None):
    """
    Fetches fully JS-rendered HTML for `url` via Browserless's /content
    REST API. Returns the HTML string on success, or None on any
    failure (missing token, network error, non-200 response) — the
    caller is expected to treat a None return as "this tier didn't
    help," the same pattern every other tier in this project follows,
    rather than raising and potentially crashing a batch scrape.
    """
    if not api_token:
        if log:
            log("    [browserless] No API token supplied, skipping browser-rendering fallback")
        return None

    if log:
        log(f"    [browserless] Fetching JS-rendered HTML for {url} ...")

    try:
        resp = requests.post(
            f"{BROWSERLESS_CONTENT_URL}?token={api_token}",
            json={"url": url},
            headers={"Content-Type": "application/json", "Cache-Control": "no-cache"},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        if log:
            log(f"    [browserless] ERROR: request failed — {e}")
        return None

    if resp.status_code != 200:
        if log:
            log(f"    [browserless] ERROR: returned HTTP {resp.status_code}")
        return None

    html = resp.text
    if not html or len(html) < 200:
        # Confirmed real signal per Browserless's own documentation:
        # an empty or near-empty response usually means the target site
        # blocked the automated browser, not that the page is genuinely
        # this short. Treated as a failure rather than usable content.
        if log:
            log(f"    [browserless] WARNING: response suspiciously short "
                f"({len(html)} chars) — site may have blocked the request")
        return None

    if log:
        log(f"    [browserless] Got {len(html)} chars of rendered HTML")
    return html
