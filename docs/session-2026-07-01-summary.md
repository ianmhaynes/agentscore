# AgentScore — Session Summary, July 1, 2026
## Day 4: Database cleanup, junk removal, and PropertyList platform fix

This document is a complete, accurate record of today's session —
written for either picking this back up fresh, or handing off to
someone else with full context. Builds directly on
`docs/session-2026-06-25-summary.md` from the prior session.

---

## Starting point

892 offices in database (after cleanup — see below), cron running
every 15 minutes, batch size 15. Session picked up from a prior
long conversation that was getting slow; context restored via the
two session summary docs uploaded at the start.

---

## What got done today

### 1. Junk office cleanup — ~112 offices deleted

The "succeeded but zero listings" bucket (378 offices at session
start) contained a large number of entries that were never going to
produce listings because they weren't real estate sales offices at
all. Identified by manual inspection of the full office list and
cleaned via SQL DELETE in Supabase's SQL Editor.

**Categories removed:**
- US and Canadian agencies — pulled in because suburb names like
  "Redlands" and "Innisfail" also exist in the US/Canada. Included
  Zillow profiles, `.ca` domains, US RE/MAX offices, Keller Williams
  Redlands CA, etc.
- Mortgage brokers — `mortgagechoice.com.au` (8+ entries across
  regions), `aussie.com.au/mortgage-broker`, Yellow Brick Road,
  multiple independent brokers
- Property valuers — `acumentis.com.au` (8+ entries), Herron Todd
  White (`htw.com.au`), The Valuer
- Lawyers and conveyancers — Finemore Walters & Story, Keylaw,
  South Geldard Lawyers, Byrne Lawyers, Property Law Centre,
  Statewide Conveyancing
- Insurance — Consolidated Insurance Brokers
- Strata managers — Archers, BCS Strata, Toowoomba Strata
- Builders — JRZ Homes, Stroud Homes
- Non-RE businesses — property maintenance, land surveyors,
  agricultural consultants, holiday accommodation, photography,
  staging
- Social media / review sites — Facebook pages (multiple), YouTube,
  RateMyAgent, Wix blog, Square site
- Individual agent profile pages on franchise sites — these will
  never have their own listings; the parent domain does
- Dead domains (DNS resolution failures) — 15 deleted via separate
  SQL query

**Final counts after cleanup:**
- 892 total offices (down from ~1,004)
- 294 with real listings (33% of all offices; 33% of scraped)
- 324 succeeded but no tier match — the real opportunity
- ~274 blocked/broken (403, timeout, SSL, rate-limited)

**Real lesson:** The 33% hit rate is now an honest number. Before
cleanup the denominator included ~112 non-agencies, inflating the
"succeeded but empty" bucket and making the problem look harder than
it is. The ceiling if all tier-matchable offices were cracked is
approximately 68% (294 + 324 = 618 of 892).

### 2. Zero-listing office analysis

Ran a breakdown query on the 602 zero-listing offices:

| Category                    | Count |
|-----------------------------|-------|
| Succeeded, no tier match    | 324   |
| Timeout                     | 159   |
| 403 blocked                 | 71    |
| SSL error                   | 18    |
| Dead domain                 | 15    |
| Other error                 | 15    |
| 429 rate limited            | 11    |

The 324 "succeeded but no tier match" are the actionable ones.
The 403/timeout/SSL/dead categories are not fixable without rotating
proxies or waiting for site owners to fix their infrastructure.

### 3. PropertyList platform — new tier 3l + URL pattern fix

**Discovery method:** Scanned all zero-listing offices for the
`propertyList-location-suburb` CSS class signature using a shell
loop against live sites. Found 9 confirmed offices on this platform:

- `scottwade.com.au` (12 listings)
- `repropertyagents.com.au` (4 listings)
- `rowlingandco.com.au` (24 homepage matches)
- `codepg.com.au` (46 homepage matches)
- `bridgebury.com.au`
- `pinpointproperty.com.au`
- `agrealty.com.au`
- `russellislandrealestate.com.au`
- `redlandscoastrealty.com.au`

**Platform characteristics:**
- Listings live on the **homepage** (`/`) — confirmed `/for-sale`
  returns 0 on most offices despite the nav link existing
- Detail URLs: `/property/{numeric-id}/{address-slug-ending-in-state}`
  e.g. `/property/136/204--32-34-miller-street-bargara-qld`
- Address: `<a title="Street, Suburb">` attribute (most reliable)
- Suburb: `<span class="propertyList-location-suburb">`
- Price: `<div class="listing-price">`
- Note: `localpropertyteam.com.au` matches the signature but lists
  Bali properties — not a QLD sales agency, safely ignored

**Two fixes required:**

1. **`extraction_tiers.py` — new function `try_propertylist_platform_pattern`
   (tier 3l):** Extracts address from `<a title>`, suburb from
   `propertyList-location-suburb` span, price from `listing-price`
   div. Deduplicates by address (each card appears twice in DOM).
   Added to pipeline before `try_elders_franchise_pattern`.

2. **`scraper.py` — URL pattern in `_looks_like_listing_url`:**
   The existing check only accepted URLs ending in 4+ digits. This
   platform's URLs end in a text slug (`-bargara-qld`), so every
   listing link was silently rejected. Added:
   ```python
   if re.search(r"^/property/\d+/[a-z0-9-]+$", path, re.IGNORECASE):
       return True
   ```
   Inserted after line 1020 (after the `/residential/` pattern check,
   before the final `return bool(...)` line).

**Confirmed working live test result:**
- `scottwade.com.au`: 12 of 12 listings extracted (tier 1 JSON-LD
  on detail pages — the platform has JSON-LD structured data, so
  tier 3l wasn't needed for extraction itself, only the URL fix)
- `codepg.com.au`: 35 of 35 listings extracted
- `rowlingandco.com.au`: 19 of 19 listings extracted

**Important note on extraction:** The detail pages on this platform
have JSON-LD structured data, so tier 1 handles extraction once the
URL pattern fix allows the scraper to find and visit the listing
pages. Tier 3l (the new extraction function) serves as a fallback
if any office on this platform lacks JSON-LD.

---

## Real, important lessons from today

### File patching — never copy via Downloads

Two file corruption incidents occurred when patching `scraper.py`:

1. First attempt: used a Python `str.replace()` on a sandbox copy,
   producing a correct output file — but `cp ~/Downloads/scraper.py`
   picked up an OLD file still in Downloads from a previous session,
   not the new patched one. Result: 354 lines deleted, file corrupted.

2. Second attempt: same mistake. Git restore (`git checkout HEAD~1`)
   used to recover both times.

3. **Correct method confirmed:** Edit the file **directly in the
   repo** using Python with the full repo path:
   ```python
   with open('/Users/ianhaynes/Downloads/agentscore_web/api/scraper.py', 'r') as f:
       lines = f.readlines()
   # ... insert lines ...
   with open('/Users/ianhaynes/Downloads/agentscore_web/api/scraper.py', 'w') as f:
       f.writelines(lines)
   ```
   Then `git add / commit / push` directly from the repo. No
   intermediate Downloads copy step.

### macOS `sed -i` has different syntax

`sed -i '1020a\ ...'` fails on macOS with "extra characters after \\"
— macOS BSD sed requires `sed -i ''` and has different multiline
append syntax. Use Python for any multiline insertion on macOS.

### Harcourts and Explore Property are JS-rendered

Both `harcourts.net/au/office/...` and `explorepropert*.com.au`
return empty raw HTML — Astro/React apps that require Browserless
to render. Not crackable with static HTTP. These are a real gap
but require Browserless budget to address.

---

## Current state at end of session

- **892 offices** in database, all legitimate real estate agencies
- **294 with real listings** (33%)
- **324 succeeded but no tier match** — still the primary opportunity
- Cron running every 15 minutes — will pick up the 9 PropertyList
  offices organically on next pass
- No robots.txt compliance — deferred again (third time). Should be
  the first priority next session.

---

## What to do next session

1. **robots.txt compliance** — deferred three times now. Must not be
   deferred again. Two confirmed gaps: `spp.net.au` and
   `townsville.century21.com.au` both explicitly disallow scraping.
   Add robots.txt checking to the scraper before visiting any URL.

2. **Continue working the 324 "succeeded but no tier match" offices**
   — after PropertyList the next clusters worth scanning for are:
   - `harcourts.net/au/office/...` pattern (3 seen in first 60,
     JS-rendered — needs Browserless or skip)
   - `explorepropert*.com.au` (3 seen, also JS-rendered)
   - Independent agency sites — scan remaining zero-listing domains
     for shared CSS class signatures using the same shell loop method
     that found PropertyList

3. **Check PropertyList cron results** — after next cron pass,
   verify the 9 confirmed PropertyList offices now show listings in
   the database. If `redlandscoastrealty.com.au/rent` shows up, the
   `/rent` URL exception should be confirmed harmless (rent listings
   would be filtered by status, not URL).

4. **Consider a second state** — QLD coverage is now solid enough
   that NSW or VIC seeding could begin using the same
   `bulk-discover-and-seed` approach.

5. **When editing Python files:** Always edit directly in the repo
   at `~/Downloads/agentscore_web/api/`, never via a Downloads
   intermediate copy. Use Python line-insertion, not macOS sed.
