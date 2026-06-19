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
  High confidence — structured JSON data, not pattern-matched.
- **Ray White Surfers Paradise** (`raywhitesurfersparadise.com.au`) —
  verified: same `INITIAL_STATE` structure present, same URL pattern.
- **Harcourts Property Hub** (`propertyhub.harcourts.com.au`) — confirmed
  this runs on a different platform entirely (Cloudhi/Rex Software, not
  Ray White's Dynamics) and added a second adapter (`CloudhiRexAdapter`)
  for it. First attempt (parsing listing cards off the index page) matched
  zero real listings on live test — the assumed card markup was wrong.
  Rebuilt against **confirmed structure from live DevTools inspection**:
  individual listing detail pages reliably expose
  `<p class="fw-bold mb-0">Property for Sale</p>` (or "Sold Property"),
  `<h1>{address}</h1>`, and `<h3>{price text}</h3>`. The adapter now
  visits the index pages only to collect listing URLs, then visits each
  listing's detail page individually for the actual data. Verified
  against fixtures built directly from two real inspected pages (one
  active, one sold) — **not yet verified against a full live scrape
  through the deployed tool**. Marked `extraction_confidence: "medium"`
  throughout (HTML-pattern based, not structured JSON like Ray White).
  Known limitations:
    - Only collects listing URLs from page 1 of `/listings/buy` and
      `/listings/sold` — no pagination yet, so offices with many
      listings will be undercounted.
    - One HTTP request per listing (in addition to the two index page
      requests) — meaningfully slower than the Ray White adapter, and
      could be slow or hit a timeout for offices with hundreds of sold
      listings.
    - Agent name extraction is a best-effort pattern match against
      rendered text near the profile link — confirmed working on the
      two inspected examples, not guaranteed robust against every page
      variant.
    - date_listed / sold_date not populated (not found in the inspected
      structure).

This strongly suggests Ray White coverage works across **any Ray White
office** running Dynamics, since the structure is a property of the
platform. Cloudhi/Harcourts coverage is real but lower-confidence and
file-page-1-only until extended.

## What's NOT yet confirmed

- **LJ Hooker, Century 21, First National** — unknown which platform
  each runs on. Could be Cloudhi (in which case `CloudhiRexAdapter` may
  already work, untested) or something else entirely. Each needs the
  same detect-first treatment before trusting results.
- **Independent agencies** — likely a long tail of different CMSs.
  Not supported at all yet.
- **Agent registration/licence number** — confirmed NOT present in
  either Ray White's or Harcourts/Cloudhi's listing data. Would require
  a separate lookup against the relevant state's real estate licensee
  register (e.g. QLD Office of Fair Trading), matched by agent name.

## Known data quirks (confirmed via live testing)

- **Ray White**: `listingState` and `status`/`statusCode` can disagree
  on the same record. **Always trust `status`/`statusCode`**.
- **Ray White**: `price` is the reliable numeric guide price even when
  `displayPrice` shows non-numeric text like "AUCTION" or "CONTACT AGENT".
- **Ray White**: a small share of sold listings (~12% in the Mermaid
  Waters sample) have no `soldPrice` recorded at all, spread across
  multiple agents and years — appears genuinely missing in Ray White's
  own data, not a scraping error. Exclude these rows from any
  variance/accuracy scoring rather than treating as zero.
- **Harcourts/Cloudhi**: price is free text ("Offers Over $X", "AUCTION",
  "Contact Agent", "Expressions of Interest", etc.) — many listings will
  have no parseable numeric price by design, same pattern the original
  Domain.com.au scraper had to handle.

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
