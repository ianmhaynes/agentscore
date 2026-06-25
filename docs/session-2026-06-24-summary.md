# AgentScore — Session Summary, June 24, 2026
## Day 1-2 of the 14-day production plan: database, Browserless, QLD scale-out

This document is a complete, accurate record of today's session —
written for either picking this back up fresh, or handing off to
someone else with full context.

---

## What got built today

### 1. Real database infrastructure (Day 1 of the plan)
- **Supabase Postgres**, connected via the **IPv4 Shared Pooler**
  specifically — the default "Dedicated Pooler" Supabase shows is
  IPv6-only on the free tier, which Vercel's serverless functions
  cannot reach. Fixed by toggling "Use IPv4 connection (Shared
  Pooler)" on Supabase's Connect panel, which changes both the
  hostname (`aws-N-{region}.pooler.supabase.com`) and username format
  (`postgres.{project_ref}`).
- **Schema** (`schema.sql`): `offices` (master list) and
  `listing_snapshots` (ONE ROW PER SCRAPE, not per listing — the
  design choice that makes historical price/days-on-market tracking
  possible later just by comparing snapshots over time).
- **`db.py`** — all database read/write logic, fully tested with
  mocked connections.
- **New endpoints**: `/api/seed-offices`, `/api/cron-scrape`,
  `/api/office-status`, `/api/db-test` (temporary diagnostic),
  `/api/bulk-discover-and-seed` (added later today, see below).
- **Vercel Cron** configured to call `/api/cron-scrape` automatically.

### 2. Two real production crashes found and fixed (via Vercel's actual function logs, not guesses)
- **`AttributeError: 'Listing' object has no attribute 'get'`** —
  `scrape_office()` returns `Listing` dataclass instances, not plain
  dicts; every new endpoint added this session wrongly assumed dicts.
  Fixed by converting via `asdict()` immediately inside
  `scrape_office_with_hard_timeout()`, the same way the existing
  working UI endpoint (`scrape_offices()`) already did.
- **Guardian Realty causing a generic 500** — initially misdiagnosed
  as several different timeout theories before checking Vercel's real
  logs, which showed the actual cause was the AttributeError above,
  not a timeout at all. A genuine hard per-office timeout (via a
  background thread) was ALSO added afterward as a real, separate
  protection — a single office with many candidate pages can exceed
  the function's time budget even though no single HTTP request
  times out.

### 3. Discovery pagination
`discover_agencies()` previously capped at Google's 20-results-per-page
limit with no pagination. Now follows the documented `nextPageToken`
chain up to `max_pages` (confirmed real: Gold Coast alone has 60+
genuine agencies, found across 4 pages).

### 4. Browserless JS-rendering fallback — a real reversal of an earlier "no," backed by new evidence
Two earlier sessions declined Playwright/browser rendering for cost
reasons, but that reasoning assumed running it as a DEFAULT, not a
fallback. After confirming via real testing that genuinely JS-gated
platforms are a small minority (~2.2%, 2 of ~90 sites tested), the
real cost as a FALLBACK ONLY fits comfortably inside Browserless's
free tier even at meaningful scale (see the QLD cost estimate below).

- **`browserless_fallback.py`** — calls Browserless's `/content` REST
  API (the page render happens on Browserless's infrastructure, not
  locally — Vercel's Python functions can't run a real browser).
- **Critical bug found via live testing**: the fallback was built only
  into `GenericFallbackAdapter`, but LJ Hooker sites are intercepted
  EARLIER by the dedicated `LJHookerAdapter`, which had no Browserless
  support — confirmed via a real curl test showing `"Matched adapter:
  lj_hooker"` with zero Browserless mention in the log. Fixed by
  adding the same two-stage fallback to `LJHookerAdapter` too.
- **Confirmed working against the REAL live Browserless API**: a
  genuine first-ever live test against `nerang.ljhooker.com.au` found
  **16 of 16 listings parsed successfully** — solving a platform gap
  that had been a confirmed dead end since the very first session of
  this entire project.
- **Two further real parsing bugs found via the live test's raw
  HTML**: (1) the page's nav bar has its own `itemprop="name"` element
  that was being matched instead of the real address heading; (2) the
  real status field can be plain `"SOLD"` with no price attached at
  all (LJ Hooker doesn't always publish a final sold price) — the
  original regex required a `$` amount in the same match. Both fixed
  by checking specific, unambiguous CSS classes first.

### 5. QLD scale estimate and the real bottleneck
Calculated a real, defensible cost/time estimate using REIQ's
published figure (~2,000 member offices, ~85% of all QLD agencies,
implying ~2,353 total) combined with today's measured rates.

- **Browserless cost is a non-issue**: ~889 calls for one full QLD
  pass (~89% of the free tier) — tight for QLD alone on the free tier,
  but the $25/month Prototyping tier comfortably covers many states.
- **The real bottleneck was cron throughput, not cost**: at the
  original once-daily, 5-offices-per-run configuration, one full QLD
  pass would take **~471 days**. Fixed (once we confirmed the account
  is Vercel PRO, not Hobby — per-minute cadence available) by
  switching to **every 15 minutes, batch size 15** — deliberately NOT
  the fastest possible option (every minute), to avoid risking
  getting entire franchises blocked by overly aggressive request
  frequency (Belle Property and McGrath already returned real
  403/429s earlier in the project). This configuration completes a
  full QLD pass in **~1.6 days**.

### 6. Bulk region seeding
Built `/api/bulk-discover-and-seed` to add many regions at once,
replacing the manual one-region-at-a-time UI workflow. Built a
28-region QLD list (greater Brisbane split into sub-areas + every
significant regional city, sourced from population data).

**Real lesson learned tonight**: even 6 regions in ONE request caused
`FUNCTION_INVOCATION_TIMEOUT` for large population centers — the real
driver isn't region COUNT, it's how many agencies each region actually
has (each needs its own Place Details lookup). Settled on running
regions **one at a time**, with a `curl --max-time 280` safety net.

---

## Final confirmed state at end of session

**1,004 real offices** seeded across all 28 QLD regions (using
`max_pages=10`, after confirming a deeper pass found MORE agencies
than `max_pages=5` did — e.g. Gold Coast went from 59-60 to exactly
100). A WiFi dropout interrupted the final deep re-run partway through
(after Mount Isa, before Bowen onward) — most regions did complete
before the interruption based on the confirmed counts, but it's worth
running the same script (`run_all_qld_regions_deep.sh`) again to fill
in any genuinely still-missing depth, since `upsert_office()` safely
skips anything already found.

**Real gap still open**: REIQ's figure implies ~2,353 total QLD
offices; 1,004 confirmed is ~43% of that estimate. Worth treating this
as a real, open question rather than assuming either number is simply
correct — REIQ's own "~85% coverage" claim is itself approximate, and
Google Places' indexing of small independent agencies is genuinely
imperfect, separate from any cap in our own code.

**The cron job is running independently of any of this** — every 15
minutes, on Vercel's servers, completely unaffected by your laptop's
connectivity. It will keep working through all 1,004 (and growing)
seeded offices regardless of whether you're at the laptop.

---

## What to do next session

1. **Re-run `run_all_qld_regions_deep.sh`** (or download it fresh if
   needed) to fill in any regions the WiFi dropout cut off — safe to
   re-run the whole thing, duplicates are a no-op.
2. **Check `/api/office-status`** for the new total once that
   completes, and decide whether 1,000-1,200ish is "enough" or whether
   the region list itself needs expanding (more of QLD's 77 LGAs) to
   close the gap toward REIQ's ~2,353 estimate.
3. **Let the cron job run for at least a day or two**, then check
   `listing_snapshots` for real accumulated data — this is the actual
   payoff of today's infrastructure work, and hasn't been observed
   yet since seeding only just finished.
4. **Consider removing `/api/db-test`** (the temporary diagnostic
   endpoint) once everything's confirmed stable, per its own docstring.
5. Continue toward the original 14-day plan's Day 4+ (broader
   platform coverage) once the QLD infrastructure is confirmed solid —
   or apply this same bulk-region-seeding approach to a second state.
