"""NPPES NPI Registry API -- free, public, no API key required.

Docs: https://npiregistry.cms.hhs.gov/api-page (JSON API, version 2.1).
Sources individual, actively-enumerated physicians (NPI-1 records) for the
physician-track cold list, filterable by state/city/postal code/taxonomy
(specialty). NPPES exposes each provider's practice-location phone number
but not an email address -- email is resolved downstream by
`outreach/builders/enrich.py`, and an organization affiliation isn't
reliably present on individual records either, so `org` is left `None` here
(Google Places / Hunter's domain search can often fill it in later).

`enumeration_type=NPI-1` covers every *individually* enumerated provider,
not just physicians -- nurses, PAs, pharmacists, and respiratory
therapists all hold their own NPIs too, and several taxonomy_description
values (confirmed live: "Critical Care", "Obstetrics & Gynecology") match
across all of them, not just MD/DO roles. `_to_candidate` filters to a
physician credential (MD/DO, tolerant of "M.D.", "MD, FACS", etc.) so this
function's results actually match its name and docstring; records with no
parseable credential are skipped rather than assumed to be physicians.

Verified live against the real endpoint while building this module:
`GET https://npiregistry.cms.hhs.gov/api/?version=2.1&state=WA&taxonomy_description=Internal+Medicine`
returns real, current provider records with no auth required.
"""

import logging

import requests

from .common import Candidate

log = logging.getLogger(__name__)

BASE_URL = "https://npiregistry.cms.hhs.gov/api/"
API_VERSION = "2.1"
TIMEOUT_SECONDS = 15
PAGE_SIZE = 200  # NPPES's hard per-request cap


def search_physicians(
    state=None, city=None, postal_code=None, taxonomy_description=None, max_results=200,
):
    """Returns a list of candidate dicts for individual (NPI-1) providers
    matching the given filters. Paginates through NPPES's 200-per-page cap
    until `max_results` is reached or the registry runs out of results.

    Never raises: a network failure or bad response logs a warning and
    returns whatever was collected so far (possibly `[]`), so one flaky
    page can't take down a larger list-building run.
    """
    results = []
    skip = 0
    while len(results) < max_results:
        page_limit = min(PAGE_SIZE, max_results - len(results))
        params = {
            "version": API_VERSION,
            "enumeration_type": "NPI-1",
            "limit": page_limit,
            "skip": skip,
        }
        if state:
            params["state"] = state
        if city:
            params["city"] = city
        if postal_code:
            params["postal_code"] = postal_code
        if taxonomy_description:
            params["taxonomy_description"] = taxonomy_description

        try:
            resp = requests.get(BASE_URL, params=params, timeout=TIMEOUT_SECONDS)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("nppes: request failed at skip=%s: %s", skip, exc)
            break

        page = data.get("results", [])
        if not page:
            break
        for record in page:
            candidate = _to_candidate(record)
            if candidate:
                results.append(candidate)
        if len(page) < page_limit:
            break  # exhausted the registry for this filter set
        skip += page_limit

    return results[:max_results]


def _is_physician_credential(credential):
    """True if `credential` (NPPES's free-text basic.credential field, e.g.
    "M.D.", "MD, FACS", "PharmD", "ARNP") contains an MD or DO token. Period-
    stripped, comma-split, exact-token match -- not a substring check, so
    "PharmD" doesn't false-positive on containing "MD"."""
    if not credential:
        return False
    tokens = [t.strip().replace(".", "") for t in credential.upper().split(",")]
    return any(t in ("MD", "DO") for t in tokens)


def _to_candidate(record):
    basic = record.get("basic") or {}
    first, last = basic.get("first_name"), basic.get("last_name")
    if not (first and last):
        return None  # not an individual record (or malformed) -- skip

    credential = basic.get("credential", "").strip(", ")
    if not _is_physician_credential(credential):
        return None  # NP/PA/RN/PharmD/etc. -- not a physician-track candidate

    name = " ".join(p for p in (first, last) if p)
    if credential:
        name = f"{name}, {credential}"

    location = _primary_location(record.get("addresses") or [])
    taxonomies = record.get("taxonomies") or []
    primary_taxonomy = next((t for t in taxonomies if t.get("primary")), taxonomies[0] if taxonomies else {})

    return Candidate(
        name=name,
        track="physician",
        source="nppes",
        phone=location.get("telephone_number"),
        role=primary_taxonomy.get("desc"),
        state=location.get("state"),
        external_id=record.get("number"),  # NPI
        tags=["npi:" + record.get("number", "")] if record.get("number") else [],
    )


def _primary_location(addresses):
    for addr in addresses:
        if addr.get("address_purpose") == "LOCATION":
            return addr
    return addresses[0] if addresses else {}
