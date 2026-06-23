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

## Client-side scrape batching (confirmed needed — real timeout hit)

**Confirmed via live testing (June 2026)**: scraping 20 offices in one
`/api/scrape` call hit Vercel's function timeout —
`Vercel Runtime Timeout Error: Task timed out after 300 seconds` — a
real ceiling, not a guess. Each office can involve several sequential
HTTP fetches (homepage detection, index pages, every individual listing
page for the Cloudhi/LJ Hooker/generic-fallback adapters), so the time
per office adds up fast across a long list.

**Fix**: the frontend now splits a long office list into batches of 5
and sends them to `/api/scrape` sequentially, waiting for each batch to
finish before starting the next, accumulating results progressively
(office results and the listings table update after every batch, not
only at the very end). This keeps each individual request safely under
the timeout. A batch that errors doesn't abort the whole run — the
frontend logs the error and continues with the next batch.

**This is explicitly a stopgap, not a real solution for national
scale.** Tested at ~20 offices; genuinely untested above that. The
underlying problem — wanting to scrape on the order of **35,000
offices/agencies across Australia** — needs real background-job
infrastructure (a job queue, a database tracking progress, a worker
process decoupled from any single HTTP request/browser tab) to do
properly. That's a real architecture rebuild, intentionally deferred:
the agreed plan is to first prove the pipeline's value at a single
city/region scale (tens of offices, which batching now supports) before
investing in infrastructure for three-orders-of-magnitude more targets.

## Session 2 findings (June 23, 2026) — 4 more confirmed patterns, 2 more known gaps

Continued the same disciplined approach (real raw HTML before any code
change) against the remaining 0-row sites from Session 1's Newtown
test. Two more confirmed, fixable patterns built; two confirmed genuine
gaps, same category as LJ Hooker's JS-loaded platform.

**Tier 3d (Reapit/Agentbox)** — extended to cover a second platform.
Confirmed `<h4>`-address + nearby-price shape shared by BOTH Crystal
Realty (Reapit/Agentbox, explicit "Contract" label for status) AND
Park Properties (Agentpoint, no Contract label — a standalone "Sold"
text node before the price instead, checked as a fallback). A real
offset bug was found and fixed in the Agentpoint fallback: the original
code computed a price-match position relative to a substring but then
sliced the FULL document with that offset directly, always pointing at
the wrong region of the page.

**Tier 3e (semi-bold/muted)** — Pilcher Residential, confirmed via live
DevTools inspection: status in `class="semi-bold"`, price in a sibling
`class="muted"` — separate elements, distinct from every
combined-string tier built so far.

**Tier 3f (ReNet heading pattern)** — Travers Gray Real Estate
(platform: ReNet, confirmed via "Marketing by ... and ReNet Real
Estate Software" footer): suburb and street address in SEPARATE `<h2>`/
`<h3>` headings (not adjacent the way tier 3's Viridity/JBRE pattern
is), status+price combined in a LATER `<h2>` ("Sold for $X").

**Real bug fixed in `_looks_like_listing_url()`**: a minimum-length
check (`len(path) > 15`) wrongly rejected Travers Gray's bare
numeric-ID listing URLs (e.g. `/21631808`, 9 characters — no slug, no
hyphens at all). Removed the length check entirely; the numeric-ID-at-
the-end requirement alone already excludes every real nav link
confirmed across every site tested in this project so far.

**Confirmed genuine gap (not fixable without a browser)**:
**BresicWhitney** shows `"Authenticated is false"` and "This property
is only available on BW.com.au, click to see more" in search results —
a real, confirmed signal that listing content is gated behind
client-side authentication/JavaScript, the same category of problem as
LJ Hooker's HubSpot platform. Not investigated further given the
explicit decision against Playwright/browser rendering.

**Travers Gray's homepage gate is a non-issue, confirmed by testing**:
initially suspected the splash-screen homepage (only one link, "Enter
Website") would block discovery entirely, since the real nav only
appears on a sub-page. Confirmed this doesn't matter — `fetch()` tries
every `CANDIDATE_INDEX_PATHS` entry directly against the domain root
regardless of how the homepage itself links to anything, so `/sold`
and `/for-sale` (already in the candidate list) are reached either way.

**Crystal Realty's real cause, found via direct curl diagnostic on the
live deployed app**: not an extraction or discovery bug at all — a
genuine DNS resolution failure. The bare domain `crystalrealty.com.au`
(no "www.") does not resolve, while `www.crystalrealty.com.au` resolves
fine — a real DNS configuration choice some sites make. Fixed in
`scrape_office()`: on a confirmed `NameResolutionError`/DNS failure for
a non-www domain, automatically retry once with "www." prepended before
giving up. Two safety guards: an already-www. domain that still fails
DNS does not retry again (no infinite loop), and non-DNS errors
(timeouts, refused connections, SSL errors) are never retried this way
since changing the hostname wouldn't fix those.

**Still open**: livingea.com.au not yet directly inspected (search
results returned only "about us" / testimonial content, no listing
data).

**A genuinely important bug found only via live production data, not
any test fixture**: every Crystal Realty listing came back
`status: "Active"` on the live deployed app, even for listings
confirmed sold (e.g. 2B Hopetoun Street Petersham, explicitly
`<label>Contract</label><div>Sold</div>` when inspected directly).
Direct `curl` against the real page revealed the cause: the real HTML
has a **newline between `</label>` and the following `<div>`**:
```html
<label class="detail-label">Contract</label>
<div class="detail-value">Sold</div>
```
The Contract-field regex's tag-skipping group, `(?:<[^>]*>)*`, only
matched directly-ADJACENT tags with zero whitespace between them — it
silently stopped at `</label>` and never reached the actual value.
Every hand-written test fixture in this project happened to place tags
with no whitespace between them, so this bug was invisible to the
entire test suite despite "passing" — it only surfaced once real
production data was checked. Fixed by allowing optional whitespace
between each skipped tag: `(?:\s*<[^>]*>\s*)*`. A new regression test
uses the exact real HTML string captured via curl, specifically so this
class of bug (real whitespace a fixture wouldn't naturally include)
can't silently reappear. This is a genuine reminder that "the tests
pass" and "this works on the real site" are not the same claim — see
`test_tier3d_contract_field_with_whitespace_between_tags` for detail.

**A second, different www. case found (Park Properties, June 2026)**:
unlike Crystal Realty's DNS resolution failure, Park Properties' bare
domain (no "www.") connects successfully (status 200, no exception at
all) but serves content yielding ZERO matching listings via every
candidate path, while "www.parkproperties.com.au" works fully (41 real
listings confirmed). Extended the retry logic: if `GenericFallbackAdapter`
finds zero listings on the bare domain, proactively retry once with
"www." before giving up — scoped specifically to the generic fallback
adapter (Ray White/Cloudhi/LJ Hooker already have their own confirmed
domain conventions, so a genuine zero-listings result from them is more
likely a real "nothing to find" than a www./non-www. quirk). Same
one-shot-only guard against looping if the www. variant also yields
nothing.

## API key persistence (browser-side only)

Both API key fields (Google Places, Anthropic) now persist across
page refreshes via the browser's own `localStorage` — purely
client-side, saved on the user's own machine, never sent to or stored
on any server except in the actual `/api/discover` or `/api/scrape`
request when a button is clicked, exactly as before this change.
Wrapped in try/catch since `localStorage` can throw in some
private-browsing modes or if disabled by the browser — in that case
the keys simply won't persist (a silent, harmless degradation), not a
broken page.

## Decision: staying plain-HTTP only (no Playwright/browser rendering)

JS-loaded sites (LJ Hooker's search-results index, the Broadbeach-style
homepage shell) cannot be read by this project's plain-HTTP approach —
not a bug, a structural limit: the listing data genuinely isn't sent by
the server until a browser executes JavaScript that fetches it
separately. This was considered and explicitly declined as something
to fix right now:

- **What it would take**: a real headless-browser service (e.g.
  Browserless, running Playwright/Puppeteer) sitting in front of or
  alongside the existing Vercel app, since Vercel's serverless Python
  functions can't run a Chromium binary themselves.
- **What it costs**: a genuine ongoing operating cost, not a one-time
  fee — Browserless prices in "Units" (~30 seconds of browser time
  each), starting around $25/month for light use, scaling up with
  volume. Visiting hundreds of listings across many offices would mean
  real, recurring spend that grows with usage, unlike the current
  approach which is effectively free.
- **How common the problem actually is**: genuinely recurring, not
  rare, but not universal either. Confirmed JS-loaded: LJ Hooker's
  index pages and the HubSpot-powered office homepage shell. Confirmed
  server-rendered (no browser needed): Ray White, Harcourts/Cloudhi,
  Belle Property, and LJ Hooker's own individual listing pages.
  Australian agencies are fragmented across many different website
  platforms (Agentpoint, HubSpot, and others) layered on top of
  separate backend CRMs (Rex, Agentbox, VaultRE, Box+Dice, etc.) — which
  website platform a given office happens to use, not which franchise
  it belongs to, determines whether its pages are server-rendered.
- **Decision (June 2026)**: not worth the ongoing cost right now.
  Revisit if there's a specific, funded reason to unlock a particular
  JS-loaded site (e.g. a client need driving real usage that would
  justify the spend).

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

## Generic Fallback Adapter — now a tiered extraction pipeline

`GenericFallbackAdapter` sits at the end of the adapter chain as a
last-resort catch-all for any site that doesn't match Ray White,
Harcourts/Cloudhi, or LJ Hooker. It was originally built from Belle
Property alone, then substantially rebuilt after a single research
session inspecting **9 more real, unrelated agency sites** (June
2026): traversgray, Richardson & Wrench Newtown, Highland Property,
Crystal Realty, Wiseberry, Viridity Real Estate, JBRE Property, Park
Properties, BresicWhitney, Pilcher Residential.

**The key finding from that session**: at least 8 genuinely different
markup shapes for the same 3 facts (address, price, sold-or-not) —
there is no single selector that works universally. What DOES
generalize is a fixed *checking order*: try the most structured, most
likely-correct source first, fall through if absent. That order is now
implemented in `extraction_tiers.py`:

1. **JSON-LD** (`schema.org/Product`) — confirmed on Wiseberry, fully
   structured (address as sub-fields, numeric price). Image URLs
   referenced `agentboxcdn.com.au`, suggesting this may generalize to
   other Agentbox-fed sites — unconfirmed beyond the one example.
2. **Open Graph / Twitter Card meta tags** — confirmed on Highland
   Property (`og:street-address`, `og:locality`, etc., plus a
   `twitter:title` embedding status and sold date as text).
3. **Known shared-template check** — confirmed **identical** markup
   (`class="prop-title pull-left/pull-right margin0"`, combined "Sold
   For $X" text) across two unrelated-looking agencies, Viridity Real
   Estate and JBRE Property — proof that distinct agencies sometimes
   run the literal same platform.
   - **3b.** The original Belle-confirmed `class="price"`/`class="address"`
     check, preserved as its own explicit tier.
   - **3c.** The original fully-generic `$`-near-text scan, kept as the
     lowest-confidence safety net below every more specific tier.
4. **LLM extraction** (optional, real cost) — the true last resort,
   only attempted when tiers 1–3c all fail to find a usable address,
   and only if the user supplies an Anthropic API key in the UI (sent
   per-request, never stored — same pattern as the Google Places key).
   Sends a trimmed slice of the page's HTML to a fast model and asks
   for structured JSON back. This is the one tier that can plausibly
   handle a genuinely novel pattern — e.g. **traversgray's price hidden
   inside a `<input type="hidden" name="extra_data[price]">` field**,
   which none of tiers 1–3c would ever catch — without a human
   inspecting the page first.

**Cost discipline, tested and verified**: `test_extraction_tiers.py`
includes `test_llm_tier_only_called_when_free_tiers_fail`, which mocks
the LLM API call to *raise an exception if invoked* and confirms it
never fires when a free tier already found a result. This is the core
guarantee behind the cost story below — the expensive tier only runs
when it's genuinely needed, not on every listing.

**Cost — discussed and explicitly NOT yet measured at real scale.**
A rough estimate (a few cases at $0.001–0.005 per listing that reaches
tier 4) put a single full pass at the user's stated ~35,000-office
target around **$1,400** — but that's one-time-per-refresh, not
ongoing, and built on guessed inputs (average listings per office,
what fraction of real sites need tier 4 at all). The agreed plan is to
get a *real* number from a bounded test — running the layered system
across one full region (~50 offices) and reading the actual bill
afterward — rather than trusting the estimate at national scale.
`GenericFallbackAdapter.llm_call_count` and the per-office log line
(`"(used LLM extraction tier N time(s) for this office)"`) exist
specifically to make that measurement possible from a real run.

**Always reports `extraction_confidence: "low"`** for every tier,
including the LLM one — there's no accuracy data yet to justify a
higher confidence label for LLM-extracted rows, so it isn't claimed.
`source_adapter` now includes which tier actually produced the row
(e.g. `generic_fallback:json_ld`, `generic_fallback:llm_extraction`),
visible in the UI's confidence-column tooltip.

**Known limitations, stated plainly:**
- None of tiers 1–3c are confirmed universal — each is confirmed on
  exactly the site(s) named above, with unknown reach beyond that.
- The `/sold/`-path convention (used as a fallback when a tier doesn't
  itself determine status) is Belle-specific in confirmation.
- The candidate listing-index paths `fetch()` tries are drawn from
  conventions seen across Ray White, Harcourts, and Belle's menu
  structure — not a guarantee any given site uses one of them. This
  remains the actual bottleneck for many of the 9 newly-inspected
  sites: even with all 4 extraction tiers ready, `fetch()` still needs
  to *find* a listing's URL in the first place, and that discovery step
  wasn't re-tested against these 9 sites in this round.
- Because `detect()` always returns `True`, this adapter is registered
  **strictly last** — see `test_generic_adapter.py`'s
  `test_adapter_order_generic_is_last`.

See `extraction_tiers.py` for the full tiered pipeline and
`test_extraction_tiers.py` for its test suite, and `scraper.py`'s
`GenericFallbackAdapter` class for how it's wired into the adapter
chain.

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
  scraper.py          Adapter-based scraping core (Ray White, Harcourts/
                       Cloudhi, LJ Hooker, generic fallback)
  extraction_tiers.py  Tiered field extraction (JSON-LD, meta tags,
                        known templates, generic scan, optional LLM
                        last resort) — used by GenericFallbackAdapter
  scoring.py          Agent variance scoring (guide vs sold price)
  discovery.py         Google Places API office discovery (Text Search
                        + Place Details — replaced an earlier
                        Domain.com.au approach, confirmed blocked in
                        production)
  templates/
    index.html        Frontend — discover area, paste URLs, view table, export
  test_scraper.py     Tests for scraper.py — Ray White & Cloudhi adapters
                        (fake INITIAL_STATE / HTML data)
  test_ljhooker_adapter.py  Tests for the LJ Hooker adapter
  test_generic_adapter.py  Tests for the generic fallback adapter
  test_extraction_tiers.py Tests for extraction_tiers.py, including the
                            LLM cost-control guarantee
  test_scoring.py     Tests for scoring.py
  test_discovery.py   Tests for discovery.py (Google Places API)
```

`scraper.py` is built as an adapter framework. Four adapters exist
today: `RayWhiteDynamicsAdapter` (high confidence, structured JSON),
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
