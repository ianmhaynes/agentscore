"""
Office discovery via Google Places API.

REPLACES an earlier Domain.com.au-based approach that was built, tested,
and deployed — then confirmed BLOCKED in production with a real HTTP 403
(Akamai-style bot detection), the same issue that affected this project
from its very first session. Rather than fight that block, this module
uses Google's Places API instead: a paid, sanctioned API with no bot
detection to defeat.

Two-step process, confirmed against Google's current documented API
shape (Places API "New", June 2026):

  1. Text Search: POST https://places.googleapis.com/v1/places:searchText
     body {"textQuery": "real estate agencies in {area}"}
     -> returns place name + place ID for each matching agency.

  2. Place Details: GET https://places.googleapis.com/v1/places/{place_id}
     header X-Goog-FieldMask: websiteUri
     -> returns the agency's actual website, which can then be fed into
     scraper.py's existing adapters exactly like a manually-typed URL.

REQUIRES a Google Cloud Platform API key with the Places API (New)
enabled. Pass it in as `api_key` — never hardcode a key in this file.

NOT YET CONFIRMED LIVE — this exact pipeline has not been run against
the real Google Places API from this codebase. The API shape is
confirmed correct from Google's own documentation, and Anthropic's
internal places_search tool returned real, correct agency data for
this exact use case during development — but that tool does not expose
the `website` field this module needs, so the full pipeline (Text
Search -> Place Details -> website) has only been verified in pieces,
not end-to-end against the live API with a real key.
"""

import time
import requests

REQUEST_TIMEOUT = 20
PLACES_API_BASE = "https://places.googleapis.com/v1/places"

# Pricing note (per Google's published SKUs, June 2026): Text Search is
# billed per request; Place Details "Contact" tier (which includes
# websiteUri) is billed separately, roughly $3 per 1000 lookups. A
# 100-agency area discovery costs on the order of a few cents in Place
# Details calls plus one Text Search call. Cheap, but not free — worth
# being aware of for repeated/large-area runs.


def discover_agencies(area, api_key, log=print, max_results=20):
    """
    area: e.g. "Mermaid Waters QLD 4218" — fed directly into a Google
    Places text query, so a normal human-readable suburb/postcode works.
    api_key: caller's Google Places API key (Places API "New" must be
    enabled on the associated Google Cloud project).
    max_results: Google Text Search returns up to 20 results per page;
    this module does not yet implement pagination beyond the first page
    (see "Known limitations" below).

    Returns a list of dicts: {name, place_id, website} — website may be
    None if Place Details didn't return one for that agency.
    """
    if not api_key:
        log("ERROR: No Google Places API key provided.")
        return []

    log(f"Searching Google Places for real estate agencies in: {area}")
    search_url = f"{PLACES_API_BASE}:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.displayName,places.id,places.formattedAddress",
    }
    body = {"textQuery": f"real estate agencies in {area}"}

    try:
        resp = requests.post(search_url, json=body, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        log(f"ERROR: Text Search request failed — {e}")
        return []

    if resp.status_code != 200:
        log(f"ERROR: Text Search returned HTTP {resp.status_code}: {resp.text[:300]}")
        return []

    data = resp.json()
    places = data.get("places", [])
    log(f"  Found {len(places)} agencies (Google Text Search, page 1 only — "
        f"see module docstring re: pagination)")

    agencies = []
    for p in places:
        name = p.get("displayName", {}).get("text", "")
        place_id = p.get("id", "")
        if not place_id:
            continue
        agencies.append({"name": name, "place_id": place_id, "website": None})

    log("Looking up each agency's website via Place Details...")
    for i, agency in enumerate(agencies, start=1):
        website = _fetch_website(agency["place_id"], api_key, log)
        agency["website"] = website
        status = website if website else "(no website on record)"
        log(f"  [{i}/{len(agencies)}] {agency['name']}: {status}")
        time.sleep(0.1)

    found = sum(1 for a in agencies if a.get("website"))
    log(f"Done. {found} of {len(agencies)} agencies have a usable website URL.")
    return agencies


def _fetch_website(place_id, api_key, log):
    url = f"{PLACES_API_BASE}/{place_id}"
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "websiteUri",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        log(f"    ERROR fetching Place Details for {place_id}: {e}")
        return None

    if resp.status_code != 200:
        log(f"    Place Details for {place_id} returned HTTP {resp.status_code}")
        return None

    data = resp.json()
    return data.get("websiteUri")
