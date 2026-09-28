"""Google Places API -- sources local physician practices / clinics by
text search (e.g. "family medicine clinic Spokane WA"). Needs
`GOOGLE_PLACES_API_KEY` (added to `api/config.py`); with no key configured
this module no-ops with a clear log message rather than crashing, matching
`api/services/stripe_client.py`'s stub-fallback house style where a
reasonable stub exists -- except here there's no meaningful stub for "search
Google's index of real places", so it degrades to an empty result list
instead (closer to `api/routes/practice.py`'s clean-refusal style, just
returning `[]` rather than raising, since this is a batch list-builder, not
a request/response endpoint with a caller to hand a 503 to).

Uses the Places API (Legacy) Text Search + Place Details endpoints -- the
simplest stable surface for this use case, no protobuf/gRPC client needed:
https://developers.google.com/maps/documentation/places/web-service/search-text
https://developers.google.com/maps/documentation/places/web-service/details
"""

import logging

import requests
from flask import current_app

from .common import Candidate

log = logging.getLogger(__name__)

TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
TIMEOUT_SECONDS = 15
DETAIL_FIELDS = "name,formatted_phone_number,international_phone_number,website,formatted_address"


def _api_key():
    return current_app.config.get("GOOGLE_PLACES_API_KEY", "")


def search_practices(query, track="physician", max_results=40, fetch_details=True):
    """`query` is a free-text Places query, e.g. "primary care clinic
    Spokane WA" or "orthopedic surgery Denver CO". Returns a list of
    candidate dicts (no email -- Places doesn't expose one; `enrich.py`
    resolves it from the returned website's domain).

    Returns `[]` immediately, with a log message, if `GOOGLE_PLACES_API_KEY`
    isn't configured. Never raises on a request failure either -- logs and
    returns whatever was collected so far.
    """
    if not _api_key():
        log.info("places: GOOGLE_PLACES_API_KEY not configured, skipping Places search for %r", query)
        return []

    try:
        resp = requests.get(
            TEXT_SEARCH_URL,
            params={"query": query, "key": _api_key()},
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("places: text search failed for %r: %s", query, exc)
        return []

    status = data.get("status")
    if status not in ("OK", "ZERO_RESULTS"):
        log.warning("places: text search for %r returned status=%s (%s)", query, status, data.get("error_message"))
        return []

    candidates = []
    for place in data.get("results", [])[:max_results]:
        details = _place_details(place["place_id"]) if fetch_details and place.get("place_id") else {}
        candidates.append(_to_candidate(place, details, track))
    return candidates


def _place_details(place_id):
    try:
        resp = requests.get(
            DETAILS_URL,
            params={"place_id": place_id, "fields": DETAIL_FIELDS, "key": _api_key()},
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("places: details lookup failed for place_id=%s: %s", place_id, exc)
        return {}
    if data.get("status") != "OK":
        return {}
    return data.get("result", {})


def _to_candidate(place, details, track):
    name = details.get("name") or place.get("name")
    phone = details.get("formatted_phone_number") or details.get("international_phone_number")
    website = details.get("website")
    address = details.get("formatted_address") or place.get("formatted_address")
    state = _state_from_address(address)

    tags = []
    if website:
        tags.append(f"website:{website}")

    return Candidate(
        name=name,
        track=track,
        source="google_places",
        phone=phone,
        org=name,
        role="Practice",
        state=state,
        external_id=place.get("place_id"),
        tags=tags,
    )


def _state_from_address(address):
    """Best-effort two-letter state code out of a formatted US address like
    '800 W 5th Ave, Spokane, WA 99204, USA'. Returns None rather than
    guessing wrong if the shape doesn't match."""
    if not address:
        return None
    parts = [p.strip() for p in address.split(",")]
    for part in parts:
        tokens = part.split()
        if len(tokens) >= 2 and len(tokens[0]) == 2 and tokens[0].isalpha() and tokens[0].isupper():
            return tokens[0]
    return None
