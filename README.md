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
  18 active listings, full agent/price/date detail. Sold listings now
  default to the site's own ~12-month window (previously forced open to
  full history via `?dateFilter=all`, which has been removed — see
  "12-month window" below).
- **Ray White Surfers Paradise** (`raywhitesurfersparadise.com.au`) —
  verified: same `INITIAL_STATE` structure present, same URL pattern.
- **Harcourts Property Hub** (`propertyhub.harcourts.com.au`) — different
  platform (Cloudhi/Rex Software). Address, price, office, agent name,
  date_listed, sold_date, and days_on_market all confirmed working
  against live data after three rounds of bug fixes (each one caught by
  comparing assumed structure against real raw HTML, not guessing):
    1. First attempt parsed listing cards off the index page — matched
       zero real listings (wrong page shape assumed).
    2. Rebuilt against confirmed detail-page tags — but the price and
       office-name regexes matched the wrong things on real pages (an
       unrelated `<h3>` repeating the address, and a font preload link
       containing the word "Harcourts").
    3. Fixed by using two confirmed dedicated CSS classes
       (`agent-name`, `agent-office`) and a bare-`<h3>`-with-no-class
       match for price, all verified against real raw HTTP responses.

## Discover Offices

A panel lets you type a suburb/postcode (e.g. "Mermaid Waters QLD 4218")
and find every real estate agency active there, across all franchises —
not just Ray White/Harcourts.

**This went through two real iterations, worth recording honestly:**

1. **First attempt: Domain.com.au's agency directory.** Built, tested
   against fixtures, deployed — then confirmed live with a real HTTP
   403. Domain blocks this exact request in production, the same
   Akamai-style bot detection that affected this project from its very
   first session. Genuine dead end for a plain HTTP approach; not a bug
   to fix, a wall to route around.
2. **Current approach: Google Places API.** Uses Google's sanctioned,
   paid API instead of scraping a site that actively blocks it:
   - **Text Search** (`POST .../v1/places:searchText`) finds every
     `real_estate_agency`-type place matching "real estate agencies in
     {area}".
   - **Place Details** (`GET .../v1/places/{place_id}`, field mask
     `websiteUri`) looks up each agency's actual website.
   - Those websites feed into the existing scrape pipeline exactly like
     a manually-typed office URL — same adapters, same detect-first
     logic, same "No known platform detected" fallback for anything
     that isn't Ray White or Harcourts/Cloudhi.

Requires the user's own Google Cloud Platform API key with Places API
("New") enabled, entered in the UI (sent to this app's server per-request
only, never stored). Roughly $0.003 per agency looked up via Place
Details' "Contact" tier, plus one Text Search call per area.

**⚠️ NOT YET CONFIRMED LIVE WITH A REAL KEY.** The request/response shape
is confirmed correct against Google's own current API documentation, and
a real test query (via a different tool, not this codebase) returned
correct live agency data for this exact use case — but the full
pipeline (Text Search → Place Details → website → feed into scraper)
has not yet been run end-to-end against the real Google Places API from
this app with a real API key. Known limitations:
  - Text Search returns up to 20 results per page; this module does not
    yet implement pagination beyond page 1, so large areas (Mermaid
    Waters had 110+ agencies via Domain) will be undercounted.
  - No retry/backoff logic for rate limits.

See `discovery.py` for the implementation and `test_discovery.py` for
tests, including missing-key and partial-failure scenarios.

Calculated client-side (in `calculate_days_on_market()`) from
`date_listed` to `sold_date` for sold listings, or to today's date for
still-active listings (i.e. "days on market so far"). Works for both
adapters once their respective date fields are populated:
  - **Ray White**: `date_listed` from `creationTime`, `sold_date` from
    `soldDate` — both already present in the structured JSON data.
  - **Harcourts/Cloudhi**: `date_listed` parsed from "Added {date}" text
    near the Property ID; `sold_date` parsed from a dedicated "Sold
    Date" section (sold listings only). Both confirmed via raw HTML
    inspection of real listing pages.

**⚠️ OPEN QUESTION — Harcourts "Added" date semantics not fully verified.**
In a live scrape (June 2026), every active Harcourts/Cloudhi listing
showed an "Added" date within the last few days of the scrape date,
which is not plausible as the true original listing date for an
established office's full active inventory. The regex itself is
confirmed correctly matching real "Added {date}" text on the page (not
a different field) — but it's unverified whether this field represents
the **original listing date** or a **last-refreshed/reindexed
timestamp** that Cloudhi updates periodically regardless of when the
property actually went on the market. Until this is confirmed (e.g. by
checking a listing known to have been on the market for months and
seeing whether its "Added" date reflects that), **treat Harcourts'
date_listed and days_on_market fields as unverified** — they may
understate true time on market significantly. Ray White's dates are
not affected by this question (confirmed reliable, structured data).

## LJ Hooker Adapter (medium confidence, but currently no discovery path) — and a national priority list

A fourth adapter, `LJHookerAdapter`, can correctly parse individual LJ
Hooker listing pages — but **cannot currently discover an office's full
listing set automatically**, a real, confirmed limitation explained
below. LJ Hooker is the **2nd-largest real estate franchise network in
Australia by office count** (~600 offices, ~6,000 people, per industry
sources — Ray White is largest at ~700+ offices/13,000 members,
Harcourts ~300+ Australian offices, Raine & Horne ~300 offices). Given
that scale, a real priority list emerged from checking franchise-size
data rather than testing networks at random:

1. **Ray White** (~700+ offices) — covered, `RayWhiteDynamicsAdapter`
2. **LJ Hooker** (~600 offices) — listing pages parseable, discovery unsolved
3. **Harcourts** (~300+ AU offices) — covered, `CloudhiRexAdapter`
4. **McGrath / Belle Property** — Belle covered via the generic
   fallback adapter (low confidence); McGrath untested
5. **Raine & Horne** (~300 offices) — untested

**The full, confirmed picture, after exhausting every standard
discovery option:**

- **Individual listing pages are genuinely scrapable** —
  `property.ljhooker.com.au/...` pages are server-rendered with
  Schema.org markup (`itemprop="identifier"` gives status+price in one
  field) and confirmed Google-indexed (hundreds of real examples found).
  `LJHookerAdapter._parse_detail_page()` correctly extracts address,
  status, price, agent name, and phone from a real page (confirmed:
  A706/517 Harris Street, Ultimo NSW, sold $1,670,000, agent John Zheng).
- **Every office homepage is the same HubSpot marketing shell**
  (confirmed at both Broadbeach, QLD and Pyrmont, NSW) — `detect()`
  matches on the `searchProfile=` URL pattern present there, not on
  listing-page-only schema (an earlier version of this adapter had that
  backwards and matched nothing; fixed).
- **The search-results index page is ALSO JS-loaded** — confirmed via
  live fetch of Pyrmont's own `/search-results?searchProfile=buy`: raw
  HTML contains only literal "listing item" placeholders, zero real
  links. This was not expected — individual listing pages being
  server-rendered did not predict the index page would not be.
- **No working sitemap exists** — `/robots.txt` lists
  `Sitemap: https://property.ljhooker.com.au/sitemap_custom.xml`, but
  that URL 404s. The standard default locations, `/sitemap.xml` and
  `/sitemap_index.xml`, also both 404. Checked directly via `curl`,
  June 2026 — not a parsing error, the files genuinely aren't there.
- **An officeId-based fallback exists in `fetch()`** for a theoretical
  alternate URL scheme, but is unconfirmed against any real office —
  it's there in case a future office turns out to need it, not a proven
  second path.

**Practical consequence**: `fetch()` returns an empty list with a clear
log explanation for every real LJ Hooker office tested so far. This
adapter currently has no way to find listing URLs on its own — it's
correct-but-unreachable, the next problem to solve being discovery, not
parsing. A possible future direction: accept individual listing URLs as
direct input (bypassing index discovery entirely), since a person can
trivially find and paste those from LJ Hooker's own site search, the
same way Domain/Google indexing already does for these specific pages.

See `scraper.py`'s `LJHookerAdapter` class (full detail in the class
docstring) and `test_ljhooker_adapter.py` for the test suite, including
cases confirming both the own-subdomain and officeId-fallback paths
behave correctly, and that a genuinely unreachable office fails
gracefully rather than crashing or fabricating data.

## Generic Fallback Adapter (low confidence)

A third adapter, `GenericFallbackAdapter`, now sits at the end of the
adapter chain as a last-resort catch-all for any site that doesn't
match Ray White or Harcourts/Cloudhi. Built from live inspection of
**Belle Property** (June 2026), but designed to be tried broadly:

- **Price**: `<div class="price">...</div>` confirmed exact on Belle —
  same class for active ("Offers from $795,000") and sold ("$1,335,000")
  listings, with a generic "any element whose text is mostly a `$`
  amount" fallback for sites that don't use this exact class.
- **Address**: `<h1 class="address">` confirmed, falling back to a plain
  `<h1>` if the class is absent.
- **Sold status comes from the URL itself** (e.g. a `/sold/` path
  segment), not page text — a genuinely different signal from both
  other adapters, confirmed via the user's own inspection of Belle's
  site structure.
- **Agent name**: pulled from `<section class="property-agents">` if
  present; left blank otherwise rather than guessing from unrelated text.

**Always reports `extraction_confidence: "low"`** — one tier below
Cloudhi's "medium." This is a meaningfully different trust level: Ray
White is structured JSON (high), Cloudhi is pattern-matching against a
*confirmed* HTML structure (medium), this is pattern-matching with
*generic fallbacks* against structure confirmed on only one site and
assumed (not verified) to generalize. The UI marks these rows with a
`?` symbol (vs `~` for medium, `✓` for high) and the rankings Excel
export visually flags them the same way it does medium/mixed rows.

**Known limitations, stated plainly:**
- `class="price"`/`class="address"` are confirmed for Belle Property
  only — whether other agencies' sites happen to use the same
  convention is unknown and will vary site to site.
- The `/sold/`-path convention for status is Belle-specific in
  confirmation; sites using a different convention (or none) will have
  every listing reported as "Active" even if some are actually sold —
  not corrected, not hidden, just a real limitation of a best-effort
  fallback.
- The candidate listing-index paths it tries
  (`/buy`, `/properties/for-sale`, `/sold`, etc.) are drawn from
  conventions seen across Ray White, Harcourts, and Belle's own menu
  structure — not a guarantee any given site uses one of them.
- Because `detect()` always returns `True`, this adapter is registered
  **strictly last** in `ADAPTERS` — see `test_generic_adapter.py`'s
  `test_adapter_order_generic_is_last` for a test that would fail loudly
  if this ordering were ever broken by a future edit.

See `scraper.py`'s `GenericFallbackAdapter` class and
`test_generic_adapter.py` for full detail.

## 12-month window

- **Ray White**: now respects the site's own default ~12-month window
  for sold listings, rather than forcing `?dateFilter=all` to pull full
  multi-year history. This both matches the "last 12 months is enough"
  requirement and reduces request volume.
- **Harcourts/Cloudhi**: no client-side or server-side 12-month filter
  applied yet — every listing found on page 1 of the index is included
  regardless of date. Since pagination isn't implemented either, real
  exposure to old listings is naturally limited for now, but this
  should be revisited once pagination is added.

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
- **Harcourts/Cloudhi sold listings frequently have NO guide price at
  all** — confirmed via live page inspection (June 2026): many properties
  carry an explicit disclaimer ("This property is being sold without a
  price & therefore a price guide cannot be provided") and the sold
  listing page only ever shows the final sold figure, never an original
  asking price. This is a genuine source-data limitation, not a scraping
  gap — unlike Ray White, which retains both `price` and `soldPrice` on
  every sold listing regardless of how it was marketed. **Practical
  effect**: most Harcourts agents will show real `total_sales` activity
  but few or zero `scored_sales`/variance results, since variance scoring
  requires both a guide and sold price. The Agent Rankings export's
  Summary Stats sheet reports per-adapter guide-price coverage so this
  gap is visible rather than silently producing an empty-looking ranking
  for an entire office.

## Architecture

```
api/
  app.py             Flask routes: page, /api/discover, /api/scrape,
                      /api/export.csv, /api/export.xlsx,
                      /api/export.rankings.xlsx
  scraper.py          Adapter-based scraping core (Ray White, Harcourts/Cloudhi)
  scoring.py          Agent variance scoring (guide vs sold price)
  discovery.py         Domain.com.au agency discovery (NOT yet confirmed live)
  templates/
    index.html        Frontend — discover area, paste URLs, view table, export
  test_scraper.py     Tests for scraper.py — Ray White & Cloudhi adapters
                        (fake INITIAL_STATE / HTML data)
  test_ljhooker_adapter.py  Tests for the LJ Hooker adapter
  test_generic_adapter.py  Tests for the generic fallback adapter
  test_scoring.py     Tests for scoring.py
  test_discovery.py   Tests for discovery.py (including a blocked-403 case)
```

`scraper.py` is built as an adapter framework. Two adapters exist today:
`RayWhiteDynamicsAdapter` (high confidence, structured JSON) and
`CloudhiRexAdapter` (medium confidence, HTML pattern matching — used by
Harcourts Property Hub). Each adapter implements:

- `detect(html)` — does this site match this platform? Checked before
  any parsing is attempted, so a site that merely resembles one
  platform without actually being it won't get silently mis-parsed.
- `fetch(domain, log)` — pull and normalize listings into the shared
  `Listing`/`Agent` dataclasses.

Adding support for a new platform means writing one new adapter class and
appending it to `ADAPTERS` in `scraper.py` — the app, frontend, and export
logic don't need to change.

Every `Listing` carries `source_adapter` and `extraction_confidence`
fields, so lower-confidence rows can be filtered out of scoring or
flagged for the reader without losing the data entirely. `scoring.py`
uses this to mark each ranked agent's confidence as `high`, `medium`,
or `mixed`.

`discovery.py` is intentionally separate from `scraper.py` — it solves a
different problem (finding *which* offices exist in an area) rather than
extracting listing data from a known office, and its reliability against
Domain.com.au is unconfirmed, unlike the adapters.

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
