"""ACGME Accreditation Data System (ADS) Public -- CSV-import fallback, not
a scraper, and here's why.

Investigated before writing this module: ADS Public
(https://apps.acgme.org/ads/Public/) is a server-rendered ASP.NET WebForms
application (Program Search / Sponsor Search / a fixed set of canned
Reports). There is no documented, stable JSON/REST API -- no OpenAPI spec,
no API docs page, nothing under a `/api/` path that returns structured data.
The only machine-somewhat-readable surface is the HTML report output itself,
which is exactly the kind of target the build's own non-negotiables warn
against: WebForms pages are driven by `__VIEWSTATE`/`__EVENTVALIDATION`
hidden fields and postback events rather than plain GET-able URLs, so a
scraper would be tightly coupled to ACGME's current ASP.NET page structure
and would silently break (not error -- silently return wrong/empty data) on
their next markup change. (ACGME also has a newer "ACGMEcloud" analytics
explorer for public program data, which is a JS single-page app -- same
problem, worse: it's rendered client-side, so a plain HTTP fetch wouldn't
see the data at all without running a real browser.)

Given that, the reliable path is: a human uses ADS Public's own Program
Search / Reports UI (it supports an "Export" to Excel/CSV on most report
views), saves the export, and this module imports that CSV. No fragile
markup-coupling, no risk of silently-wrong data, and it's a five-minute
manual step that only needs repeating when the list needs refreshing.

Expected CSV columns (matches ADS Public's own report export headers,
case-insensitive, extra columns ignored):
    Program Name, Sponsoring Institution, Specialty, City, State,
    Program Director Name, Program Director Email, Website
"Program Director Email" is frequently blank in ACGME's export -- when it
is, the candidate is still returned (email=None) for `enrich.py` to resolve.
"""

import csv
import logging

from .common import Candidate

log = logging.getLogger(__name__)

# Accepted header aliases -> normalized field name (case-insensitive match).
_HEADER_ALIASES = {
    "program name": "program_name",
    "program": "program_name",
    "sponsoring institution": "org",
    "institution": "org",
    "specialty": "specialty",
    "city": "city",
    "state": "state",
    "program director name": "name",
    "director name": "name",
    "program director email": "email",
    "director email": "email",
    "email": "email",
    "website": "website",
}


def import_acgme_csv(path, track="physician"):
    """Reads a CSV exported from ACGME ADS Public's Program Search / Reports
    UI and returns a list of candidate dicts (one per program/program
    director row). Malformed rows (missing both a program name and a
    director name) are skipped with a warning rather than raising, since a
    hand-exported CSV is exactly the kind of input likely to have a stray
    blank row or two.
    """
    candidates = []
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            field_map = _map_headers(reader.fieldnames or [])
            for i, raw_row in enumerate(reader):
                row = {field_map[k]: v for k, v in raw_row.items() if k in field_map}
                candidate = _to_candidate(row, track)
                if candidate:
                    candidates.append(candidate)
                else:
                    log.warning("acgme: skipping unusable row %d in %s", i, path)
    except OSError as exc:
        log.error("acgme: could not read %s: %s", path, exc)
        return []
    return candidates


def _map_headers(fieldnames):
    mapping = {}
    for raw in fieldnames:
        key = (raw or "").strip().lower()
        if key in _HEADER_ALIASES:
            mapping[raw] = _HEADER_ALIASES[key]
    return mapping


def _to_candidate(row, track):
    program_name = (row.get("program_name") or "").strip()
    director_name = (row.get("name") or "").strip()
    org = (row.get("org") or "").strip() or program_name
    name = director_name or program_name
    if not name:
        return None

    role = f"Program Director — {program_name}" if director_name and program_name else "Program Director"
    tags = ["acgme_csv_import"]
    if row.get("specialty"):
        tags.append(f"specialty:{row['specialty'].strip()}")
    if row.get("website"):
        tags.append(f"website:{row['website'].strip()}")

    return Candidate(
        name=name,
        track=track,
        source="acgme_csv",
        email=row.get("email"),
        org=org or None,
        role=role,
        state=(row.get("state") or "").strip() or None,
        external_id=None,
        tags=tags,
    )
