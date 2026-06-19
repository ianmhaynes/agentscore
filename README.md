# AgentScore — Listing Register

A hosted tool to scrape active and sold property listings (address, price,
agent, dates) from real estate agency websites, starting with Ray White's
Dynamics-platform offices.

## How it works

Paste a list of office website addresses, click "Scrape listings". The
tool fetches each office's `/properties/for-sale` and
`/properties/sold?dateFilter=all` pages with a plain HTTP request (no
browser needed) and parses the `window.INITIAL_STATE` JSON that's already
server-rendered into the page. Results show in a table and can be
downloaded as CSV or Excel.

## What's confirmed working (tested live, June 2026)

- **Ray White Mermaid Waters** (`raywhitemermaidwaters.com.au`) — verified:
  18 active listings, 262 sold listings, full agent/price/date detail.
- **Ray White Surfers Paradise** (`raywhitesurfersparadise.com.au`) —
  verified: same `INITIAL_STATE` structure present, same URL pattern.

This strongly suggests the approach works across **any Ray White office**
running the Dynamics platform, since the structure is a property of the
platform, not the individual office site.

## What's NOT yet confirmed

- **Other franchises** (Harcourts, LJ Hooker, Century 21, First National) —
  unknown whether they run an identical Dynamics build with the same field
  names, or a different version. Each needs to be tested individually
  before trusting this tool's output for them. If a site doesn't match the
  Ray White adapter's `detect()` check, the tool will report
  "No known platform detected" for that office rather than guessing.
- **Independent agencies** — almost certainly use different platforms
  entirely (Box+Co, Vault, custom WordPress, etc.) with no
  `window.INITIAL_STATE`. Not supported at all yet. Adding support means
  writing a new adapter per platform (see Architecture below).
- **Agent registration/licence number** — confirmed NOT present anywhere
  in this data (listing or agent objects). Getting this field would
  require a separate lookup against the relevant state's real estate
  licensee register (e.g. QLD Office of Fair Trading), matched by agent
  name — not yet built.

## Known data quirks (confirmed via live testing)

- `listingState` and `status`/`statusCode` can disagree on the same
  record (one showing "Active" while the other shows "Sold"). **Always
  trust `status`/`statusCode`** — `listingState` appears to not always be
  kept in sync.
- `price` is the reliable numeric guide price even when `displayPrice`
  shows non-numeric text like "AUCTION" or "CONTACT AGENT".
- A small share of sold listings (~12% in the Mermaid Waters sample) have
  no `soldPrice` recorded at all, spread across multiple agents and years
  — not an error, appears to be genuinely missing in Ray White's own data
  (auction "price withheld" type listings). Exclude these rows from any
  variance/accuracy scoring rather than treating as zero.

## Architecture

```
api/
  app.py          Flask routes: page, /api/scrape, /api/export.csv, /api/export.xlsx
  scraper.py       Adapter-based scraping core
  templates/
    index.html     Frontend (paste URLs, view table, export)
  test_scraper.py  Tests using fake INITIAL_STATE data (no network needed)
```

`scraper.py` is built as an adapter framework on purpose, even though only
one adapter exists today (`RayWhiteDynamicsAdapter`). Each adapter
implements:

- `detect(html)` — does this site match this platform? Checked before
  any parsing is attempted, so a site that merely resembles Ray White's
  structure without actually being it won't get silently mis-parsed.
- `fetch(domain, log)` — pull and normalize listings into the shared
  `Listing`/`Agent` dataclasses.

Adding support for a new platform means writing one new adapter class and
appending it to `ADAPTERS` in `scraper.py` — the app, frontend, and export
logic don't need to change.

Every `Listing` carries `source_adapter` and `extraction_confidence`
fields, so lower-confidence extraction methods (e.g. a future generic
JSON-LD or regex fallback adapter for independents) can be filtered out
of scoring calculations downstream without losing the data entirely.

## Running locally

```bash
cd api
pip install -r requirements.txt
python3 app.py
```

Visit http://localhost:5050

## Testing without real network access

```bash
cd api
python3 test_scraper.py
```

This validates the parsing/normalization logic against a fake
`INITIAL_STATE` blob shaped exactly like the real, confirmed structure —
useful for catching bugs without needing to hit live sites.

## Next steps

1. Test against a Harcourts/LJ Hooker/Century 21/First National Gold
   Coast office to find out whether the Dynamics structure is shared
   across franchises, or Ray White-specific.
2. If shared: confirm the existing adapter just works for them too.
   If different: write a second adapter following the same pattern.
3. Decide on registration-number lookup (separate licensee register
   integration) if that field is still required for the client.
4. Variance/accuracy scoring (guide vs sold price per agent) as a
   second stage once enough offices are scraped — same A/B/C grading
   model used in the original AgentScore proof of concept.
