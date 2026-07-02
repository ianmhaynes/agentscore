"""
browserless_fallback.py — Optional headless-browser rendering, used
ONLY as a last resort when a site's listing data isn't reachable via
plain HTTP at all (confirmed JS-gated platforms, e.g. LJ Hooker's
HubSpot generation, BresicWhitney's Rex CRM delivery layer).

WHY THIS EXISTS: two genuine attempts at evaluating Playwright earlier
in this project were both declined for cost reasons, reasoning that
ran ANY scrape through a real browser by default. Confirmed via real
testing across ~90 sites (June 2026) that genuinely JS-gated platforms
are a small minority (~2%) — used ONLY as a fallback, not a default.

HOW IT WORKS: Firecrawl runs the actual headless browser on its own
infrastructure — Vercel's serverless Python functions cannot run a
real Chrome binary locally, so this module makes a plain HTTP POST to
Firecrawl's /v1/scrape endpoint, which returns fully JS-rendered HTML
as a plain string. That HTML is then fed through the EXACT SAME
extraction_tiers.py pipeline used everywhere else in this project —
no separate parsing logic needed, since once JavaScript has run, the
result is still just HTML.

Previously used Browserless (/content endpoint). Switched to Firecrawl
(July 1, 2026) for better JS rendering, proxy infrastructure, and a
more generous free tier (1,000 credits/month vs Browserless).

REQUIRES a Firecrawl API key, read from the FIRECRAWL_API_KEY
environment variable (set in Vercel). The function also accepts an
explicit api_token parameter for backwards compatibility with callers
that previously passed a Browserless key — if supplied it takes
precedence, otherwise falls back to the env var.

Confirmed live against real JS-rendered real estate sites (July 2026).
"""
import os
import requests

REQUEST_TIMEOUT = 45  # browser rendering genuinely takes longer than a plain HTTP request
FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v1/scrape"


def fetch_rendered_html(url, api_token=None, log=None):
    """
    Fetches fully JS-rendered HTML for `url` via Firecrawl's /v1/scrape
    REST API. Returns the HTML string on success, or None on any
    failure (missing token, network error, non-200 response) — the
    caller is expected to treat a None return as "this tier didn't
    help," the same pattern every other tier in this project follows,
    rather than raising and potentially crashing a batch scrape.

    api_token: if supplied, used directly (backwards compat with
    Browserless callers). Otherwise reads FIRECRAWL_API_KEY from env.
    """
    token = api_token or os.environ.get("FIRECRAWL_API_KEY", "").strip()

    if not token:
        if log:
            log("    [firecrawl] No API key supplied or set in FIRECRAWL_API_KEY env var — skipping")
        return None

    if log:
        log(f"    [firecrawl] Fetching JS-rendered HTML for {url} ...")

    try:
        resp = requests.post(
            FIRECRAWL_SCRAPE_URL,
            json={
                "url": url,
                "formats": ["html"],       # return raw HTML, not markdown
                "waitFor": 2000,           # ms to wait for JS to settle
                "onlyMainContent": False,  # we need the full page, not just article content
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        if log:
            log(f"    [firecrawl] ERROR: request failed — {e}")
        return None

    if resp.status_code == 402:
        if log:
            log("    [firecrawl] ERROR: API credits exhausted (HTTP 402)")
        return None

    if resp.status_code == 401:
        if log:
            log("    [firecrawl] ERROR: invalid API key (HTTP 401)")
        return None

    if resp.status_code != 200:
        if log:
            log(f"    [firecrawl] ERROR: returned HTTP {resp.status_code} — {resp.text[:200]}")
        return None

    try:
        data = resp.json()
    except Exception as e:
        if log:
            log(f"    [firecrawl] ERROR: could not parse JSON response — {e}")
        return None

    if not data.get("success"):
        if log:
            log(f"    [firecrawl] ERROR: success=false in response — {data.get('error', 'unknown')}")
        return None

    # Firecrawl returns {"success": true, "data": {"html": "...", "metadata": {...}}}
    html = (data.get("data") or {}).get("html", "")

    if not html or len(html) < 200:
        if log:
            log(f"    [firecrawl] WARNING: response suspiciously short "
                f"({len(html)} chars) — site may have blocked the request")
        return None

    if log:
        log(f"    [firecrawl] Got {len(html)} chars of rendered HTML")
    return html
