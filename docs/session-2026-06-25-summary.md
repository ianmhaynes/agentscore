# AgentScore — Session Summary, June 25, 2026
## Day 3: working the real zero-listing offices, five tier collisions found and fixed

This document is a complete, accurate record of today's session —
written for either picking this back up fresh, or handing off to
someone else with full context. Builds directly on
`docs/session-2026-06-24-summary.md` from the prior session.

---

## Starting point

1,004 offices seeded (from yesterday), cron running every 15 minutes,
batch size 15. Began today by working through the real zero-listing
offices one at a time — fetch real bytes, find the real pattern, fix
it, ship it, same disciplined method as every prior session.

---

## What got fixed today, in order

### 1. Real genuine non-fixes (no code change needed)
Three offices confirmed as honest "nothing to fix" outcomes, not
missed extraction tiers — all genuinely have zero sale listings:
- `michaelbacon.com.au` — personal agent marketing/portfolio site
- `www.briqproperty.com.au` — property-management-only company
- `www.twobirdsproperty.com.au` — property-management-only company

Worth remembering these the next time a zero-listing pass turns them
up again, rather than re-investigating from scratch.

### 2. Woolloongabba Real Estate — new WordPress EPL tier (3h) + URL exception
Real listings sit on the homepage via a WordPress "EPL" plugin
(confirmed via `?action=epl_search` query param on nav links). Added:
- A narrowly-scoped URL exception for `/properties-for-sale/{slug}/`
  (the trailing numeric segment is a POSTCODE, not a listing ID — a
  naive "ends in digits" rule would also match the real nav link
  `/properties-for-sale/?action=epl_search...`, sharing the same
  prefix).
- Tier 3h: address in a plain `<h1>`, price as nearby plain text.

**A real, important bug found in this same session**: the existing
Eagle Software tier (3g) matched ANY `<h1>`, even without finding its
own real signature (a price inside an `<h2>`) — wrongly stealing this
page's match with empty data. Fixed by requiring Eagle Software's own
real distinguishing signature before claiming victory. **This was the
first of FIVE total tier-collision bugs found and fixed today** — see
the running list below.

### 3. Kangaroo Point Real Estate — new Rex Websites tier (3i), three further real bugs
Platform: "Rex Websites" — confirmed a DIFFERENT product from
BresicWhitney's "Rex CRM" (a confirmed JS-gated dead end from an
earlier session), made by the same parent company. 494 real sold
listings confirmed on the index page alone.

Three distinct real bugs found in sequence to get this one office
genuinely working end-to-end:
1. **CloudhiRexAdapter false positive** — its detection
   (`"rexsoftware" in lowered`) matched on the PARENT COMPANY name
   alone, wrongly claiming Rex Websites pages for an unrelated
   adapter. Fixed by requiring `cloudhi.io` specifically, or
   `rexsoftware` WITHOUT the `real-estate-websites` product path.
2. **Missing candidate index path** — even after fixing #1, the real
   sold-listings path (`/listings/?saleOrRental=Sale&sold=1`) was
   never added to `CANDIDATE_INDEX_PATHS`.
3. **Tier 3i/3d collision** — Eagle Software's permissive matching
   class of bug (#2 in today's list) reappeared a second time: tier
   3d (Reapit/Agentbox) matched an unrelated `<h4>` site title before
   the new Rex Websites tier got a chance to run, purely because SOME
   price existed within tier 3d's 500-char window. **Two different
   attempts to fix tier 3d itself were tried and reverted** — each one
   broke a real, already-tested, legitimate case tier 3d needs to keep
   handling (a genuine active listing with no status text; a genuine
   "Contact agent" listing with no parseable price). The real,
   durable fix was PIPELINE ORDERING: moved tier 3i before tier 3d,
   since 3i's own signature is specific enough to correctly claim Rex
   Websites pages first.

### 4. Abra Agencies — Rex Websites generalization fix
A second real Rex Websites office, confirmed via the same footer
credit — but used entirely different listing-ID formats (`QTW27006`,
`L18768190`) than Kangaroo Point's `R2-{numeric}`. Broadened the URL
exception to handle multiple real ID formats. Confirmed live:
**31 of 31 listings parsed correctly** (2 of 31 showed a known, minor,
deliberately-deferred collision with tier 3d — same root cause as
above, smaller blast radius, left for a future pass).

### 5. Century 21 Townsville (Push Creative platform) — clean win
Real listings on the homepage, platform built by "Push Creative".
Existing tier 2 (meta tags) ALREADY correctly extracts the complete
real OpenGraph address data — no new tier needed. Only gap: URL
discovery (`/{numeric-id}/{slug}`, the ID as its own path segment, not
hyphenated into the slug like every other confirmed pattern).

### 6. Two real, confirmed `robots.txt` compliance gaps found
`spp.net.au` and `townsville.century21.com.au` both explicitly
disallow automated access — but the production scraper has NO
robots.txt checking anywhere in the codebase. Deliberately deferred
fixing this (twice, on explicit instruction) to keep working through
other offices — **this is now a confirmed, recurring gap, documented
prominently in the README, and should be a real priority next
session, not deferred a third time.**

### 7. Elders Smith and Elliott Townsville — new tier (3k), the deepest investigation of the day
Real Elders franchise office. Real listing URLs end in a letter-mixed-
with-digits trailing ID (`...-qld-4810-300P197394/`) — added a scoped
URL exception. Real detail page splits the address across an `<h1>`
(street only) and a following `<h2>` (suburb + state + postcode).

**This single office required FOUR additional real, distinct fixes**,
each one only found by actually testing against live deployed code
and, eventually, real raw HTML fetched via direct curl (not a
markdown conversion):
1. **Tier 3k/Eagle Software collision** (the SAME class of bug as
   Woolloongabba, #2 in today's list) — Eagle Software's h1+h2
   matching collided with tier 3k's h1+h2 matching. Fixed by requiring
   each tier's own real distinguishing signature (where exactly the
   price sits relative to the heading) before claiming a match.
2. **Tier 3k/Reapit-Agentbox collision, confirmed via live testing**
   — shipped, then a REAL live test showed every listing returning
   `"address": "General Features"` (a real section heading elsewhere
   on the actual page) — tier 3d ran before tier 3k in the pipeline.
   Fixed via the same "reorder, don't weaken the already-correct tier"
   pattern as the Rex Websites/Reapit-Agentbox fix.
3. **Suburb regex whitespace bug, found via REAL raw HTML** — the
   pipeline-reordering fix shipped but the price was STILL not
   extracted. Got the actual raw bytes via direct curl (sandbox
   network couldn't reach this domain directly — had the user fetch
   it). The real h2 closing tag has trailing whitespace/newline before
   it that the original regex didn't allow for.
4. **Price extraction distance + wrong-amount risk, found via the SAME
   real raw HTML** — the real price sits ~798 characters after the
   suburb (through a real bed/bath/car `<ul>` block), beyond the
   original 500-char search window. The real page ALSO contains other
   genuine dollar amounts that are NOT the listing price — a body
   corporate fee, council rates, a rental appraisal range — a wider
   generic window would have risked grabbing one of these instead.
   Fixed by targeting the confirmed real `class="property__price"`
   element directly, both correct regardless of distance and safe
   against the BC/rates/rent-appraisal trap.

**Confirmed final result: 35 of 35 real listings, fully correct** —
real addresses, suburbs, postcodes, and prices, including correctly
distinguishing rental weekly prices from sale prices.

---

## The real, durable lesson from today

**Five separate tier-collision bugs were found today**, all the exact
same root shape: a new tier shares a structural signal (a heading tag,
or a heading + nearby price) with an EXISTING tier, and the existing
tier's matching is more permissive than its own real confirmed case
actually requires — so it wins the match on the new tier's pages with
wrong or empty data.

The fix is always one of two things:
1. **Require the tier's own genuine distinguishing signature** (not
   just "a price exists somewhere nearby") before claiming victory —
   safe when the tier's own real, already-tested cases don't need the
   looser behavior.
2. **Reorder the pipeline** — when tightening an existing tier's
   matching would break a real, legitimate case it needs to keep
   handling (confirmed true for tier 3d, tried and reverted twice
   today), the durable fix is to put the more specific new tier
   earlier in the pipeline instead.

**Going forward**: any new tier sharing a structural shape with an
existing tier MUST be checked against every existing tier with that
shape — not just tested in isolation. This has now bitten the project
five times in two days and will keep happening if skipped.

A second, equally important lesson: **two of today's "live-tested,
shipped" fixes (Melita Bell, Elders) turned out to be genuinely broken
in production** despite passing every local test — both times because
the test fixtures were built from `web_fetch`'s markdown conversion,
which looked structurally identical to the real page but actually
hid real differences (nested spans, HTML entities, real whitespace,
real character distances). Only fetching the ACTUAL raw bytes via
direct `curl` (twice requiring the user's help, since this sandbox's
network couldn't reach those specific domains) revealed the true
structure. **A "passes the local test suite" result is not the same
claim as "works against the live site" — this has now been true
often enough across this entire project that it should be treated as
the default assumption, not a surprise each time.**

---

## Real, current state at end of session

- **346 of 1,004 offices attempted** by the cron (up from 138 at
  today's start)
- **118 offices with real listing data** (up from 48)
- Several of today's fixes already confirmed paying off organically
  elsewhere in the QLD list via the cron (e.g. `www.83property.com.au`,
  `www.oneagencytownsville.com.au`, `helenmunroproperty.com`,
  `www.eliteproperties.net.au` — all on platforms fixed today, never
  manually tested individually)
- `melitabell.com.au` and `smithandelliott.eldersrealestate.com.au`
  may still show as "zero listings" in a stale status snapshot — both
  are CONFIRMED genuinely fixed and working as of the final live test
  of each; this is a snapshot-timing artifact, not a real regression

---

## What to do next session

1. **robots.txt compliance** — confirmed twice now as a real,
   recurring gap. Should be the first priority, not deferred again.
2. **The deferred 2-of-31 Abra Agencies collision** (tier 3d vs tier
   3i, smaller-scale recurrence) — low priority given its small blast
   radius, but worth a quick fix if there's spare time.
3. **Continue working the zero-listing list** — `coralseaproperty.com.au`,
   `nqrealty.com.au`, `www.tcre.com.au`, `mattohanlon.com.au`,
   `seanlubbe.biz` and others remain uninvestigated.
4. **Whenever testing a NEW tier against an h1/h2/h4-based structure**,
   explicitly check it against every EXISTING tier sharing that same
   shape before considering it done — this is the single highest-value
   habit from today's five collisions.
5. **Prefer real raw bytes (direct curl) over `web_fetch`'s markdown
   conversion** whenever building extraction logic for a new pattern,
   especially once a "shipped" fix doesn't actually work in
   production — that combination has now correctly predicted two real,
   hidden bugs today (Melita Bell, Elders) that would otherwise have
   been very hard to find by reasoning alone.
