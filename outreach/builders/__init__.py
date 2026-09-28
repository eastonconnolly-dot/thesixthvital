"""List builders for RPSAS's cold-outreach pipeline.

Each builder exposes one or more `search_*`/`import_*` functions that return
a list of **candidate dicts** — a normalized, pre-Lead shape:

    {
        "name": str,
        "email": str | None,
        "phone": str | None,
        "org": str | None,
        "role": str | None,
        "state": str | None,
        "track": "applicant" | "physician" | "program",
        "source": str,          # e.g. "nppes", "hcahps", "acgme_csv", "google_places", "advisors"
        "external_id": str | None,  # NPI, facility id, etc. -- used for dedup
        "tags": [str, ...],
    }

Builders never write to the database and never construct `Lead` rows
directly -- that's `outreach/builders/enrich.py`'s job (email
finding/verification, dedup, suppression-list checking, then `Lead`
creation). Keeping builders as pure "fetch candidates" functions makes them
trivially testable with a mocked HTTP layer and reusable outside the web app
(e.g. from a one-off script).
"""

from .common import Candidate, dedupe_by_key  # noqa: F401
