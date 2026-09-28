"""CMS Hospital Care Compare -- HCAHPS dataset, via data.cms.gov's public
Provider Data API. Free, no API key.

Docs: https://data.cms.gov/provider-data/dataset/dgck-syfz (dataset landing
page; the "API" tab there documents the query params). Endpoint format
confirmed live while building this module:

    GET https://data.cms.gov/provider-data/api/1/datastore/query/dgck-syfz/0
        ?limit=...&conditions[0][property]=state&conditions[0][value]=WA&conditions[0][operator]==

The dataset is one row per (hospital, HCAHPS survey question) -- a single
hospital shows up dozens of times, once per measure. `search_hospitals`
fetches the "always communicated well" measure (a reasonable single proxy
row per hospital) and collapses to one candidate per `facility_id`, since
what we actually want out of this dataset is a list of *hospitals/health
systems* (program-track leads: the institution buys cohort training, not an
individual clinician) with their star rating as a talking point, not the
raw per-measure rows.
"""

import logging

import requests

from .common import Candidate

log = logging.getLogger(__name__)

DATASET_ID = "dgck-syfz"  # "Patient survey (HCAHPS) - Hospital"
BASE_URL = f"https://data.cms.gov/provider-data/api/1/datastore/query/{DATASET_ID}/0"
TIMEOUT_SECONDS = 20
PAGE_SIZE = 500

# One representative measure per hospital, so each facility appears once
# per page rather than ~20x (once per HCAHPS question).
DEFAULT_MEASURE_ID = "H_COMP_1_A_P"  # "nurses always communicated well"


def search_hospitals(state=None, measure_id=DEFAULT_MEASURE_ID, max_results=200):
    """Returns a list of candidate dicts, one per hospital, track="program".
    `tags` carries the chosen measure's answer percentage as a talking
    point (e.g. "hcahps_H_COMP_1_A_P_pct:76").

    Never raises: a request failure logs a warning and returns whatever was
    collected so far.
    """
    conditions = [{"property": "hcahps_measure_id", "value": measure_id, "operator": "="}]
    if state:
        conditions.append({"property": "state", "value": state, "operator": "="})

    params = {"limit": min(PAGE_SIZE, max_results)}
    for i, cond in enumerate(conditions):
        for k, v in cond.items():
            params[f"conditions[{i}][{k}]"] = v

    try:
        resp = requests.get(BASE_URL, params=params, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("hcahps: request failed: %s", exc)
        return []

    rows = data.get("results", [])
    candidates, seen_facility_ids = [], set()
    for row in rows:
        facility_id = row.get("facility_id")
        if not facility_id or facility_id in seen_facility_ids:
            continue
        seen_facility_ids.add(facility_id)
        candidates.append(_to_candidate(row, measure_id))
        if len(candidates) >= max_results:
            break
    return candidates


def _to_candidate(row, measure_id):
    pct = row.get("hcahps_answer_percent")
    tags = []
    if pct and pct not in ("Not Applicable", ""):
        tags.append(f"hcahps_{measure_id}_pct:{pct}")

    return Candidate(
        name=row.get("facility_name"),
        track="program",
        source="hcahps",
        org=row.get("facility_name"),
        role="Hospital / Health System",
        phone=row.get("telephone_number"),
        state=row.get("state"),
        external_id=row.get("facility_id"),
        tags=tags,
    )
