"""Email finding/verification (Hunter or Apollo), dedup, and bounce
suppression -- the one place candidate dicts from the other builders turn
into real `Lead` rows.

Needs `HUNTER_API_KEY` or `APOLLO_API_KEY` (added to `api/config.py`).
Neither configured means email-finding degrades cleanly: a candidate that
arrived without an email (NPPES, HCAHPS, Places, most of ACGME) simply can't
be turned into a `Lead` (the model requires a non-null email), so it's
counted and skipped rather than raising -- the stub-vs-refusal choice here
is "skip with a clear reason", since there's no meaningful stub for "guess
someone's real email address".
"""

import logging
import os
import sys

_API_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "api"))
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

import requests
from flask import current_app

from extensions import db
from models import Lead, SuppressedEmail

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 15
HUNTER_BASE_URL = "https://api.hunter.io/v2"
APOLLO_BASE_URL = "https://api.apollo.io/v1"


def _hunter_configured():
    return bool(current_app.config.get("HUNTER_API_KEY"))


def _apollo_configured():
    return bool(current_app.config.get("APOLLO_API_KEY"))


# ── email finding ────────────────────────────────────────────────────────

def guess_domain_hunter(org_name):
    """Hunter's Domain Search by company name -- returns a bare domain
    (e.g. "example.com") or None. Requires HUNTER_API_KEY."""
    if not (_hunter_configured() and org_name):
        return None
    try:
        resp = requests.get(
            f"{HUNTER_BASE_URL}/domain-search",
            params={"company": org_name, "api_key": current_app.config["HUNTER_API_KEY"], "limit": 1},
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        return data.get("domain")
    except (requests.RequestException, ValueError) as exc:
        log.warning("enrich: hunter domain-search failed for %r: %s", org_name, exc)
        return None


def find_email_hunter(first_name, last_name, domain):
    """Hunter's Email Finder. Returns (email, confidence 0-1) or (None, 0.0)."""
    if not (_hunter_configured() and domain and first_name and last_name):
        return None, 0.0
    try:
        resp = requests.get(
            f"{HUNTER_BASE_URL}/email-finder",
            params={
                "domain": domain, "first_name": first_name, "last_name": last_name,
                "api_key": current_app.config["HUNTER_API_KEY"],
            },
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        email = data.get("email")
        score = data.get("score")  # Hunter returns 0-100
        return (email, (score or 0) / 100.0) if email else (None, 0.0)
    except (requests.RequestException, ValueError) as exc:
        log.warning("enrich: hunter email-finder failed for %s %s @ %s: %s", first_name, last_name, domain, exc)
        return None, 0.0


def verify_email_hunter(email):
    """Hunter's Email Verifier. Returns a confidence 0-1 (0 if unconfigured
    or the lookup fails -- callers should treat that as 'unverified', not
    'known bad')."""
    if not (_hunter_configured() and email):
        return 0.0
    try:
        resp = requests.get(
            f"{HUNTER_BASE_URL}/email-verifier",
            params={"email": email, "api_key": current_app.config["HUNTER_API_KEY"]},
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        return (data.get("score") or 0) / 100.0
    except (requests.RequestException, ValueError) as exc:
        log.warning("enrich: hunter email-verifier failed for %s: %s", email, exc)
        return 0.0


def find_email_apollo(first_name, last_name, org_name):
    """Apollo's People Match endpoint. Returns (email, confidence) or
    (None, 0.0). Apollo doesn't return a numeric confidence score on this
    endpoint, so a found email is treated as confidence 0.7 (moderate,
    matches Apollo's own "not guaranteed" framing) rather than 1.0."""
    if not (_apollo_configured() and first_name and last_name):
        return None, 0.0
    try:
        resp = requests.post(
            f"{APOLLO_BASE_URL}/people/match",
            json={
                "api_key": current_app.config["APOLLO_API_KEY"],
                "first_name": first_name, "last_name": last_name,
                "organization_name": org_name,
            },
            timeout=TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        person = resp.json().get("person") or {}
        email = person.get("email")
        return (email, 0.7) if email and "email_not_unlocked" not in email else (None, 0.0)
    except (requests.RequestException, ValueError) as exc:
        log.warning("enrich: apollo people/match failed for %s %s @ %r: %s", first_name, last_name, org_name, exc)
        return None, 0.0


def find_and_verify_email(candidate):
    """Fills in `candidate["email"]` in place if missing, trying Hunter
    first (domain-search then email-finder), then Apollo, in that order.
    Returns the (possibly unchanged) candidate. Never raises -- a candidate
    that can't be resolved just keeps `email=None` and is skipped later by
    `ingest_candidates`."""
    if candidate.get("email"):
        return candidate  # already has one (e.g. from advisors.py's scrape)
    candidate.setdefault("email", None)

    name_parts = (candidate.get("name") or "").split()
    if len(name_parts) < 2:
        return candidate
    first_name, last_name = name_parts[0], name_parts[-1]

    if _hunter_configured():
        domain = guess_domain_hunter(candidate.get("org")) if candidate.get("org") else None
        email, confidence = find_email_hunter(first_name, last_name, domain)
        if email:
            candidate["email"] = email
            candidate.setdefault("tags", []).append(f"email_confidence:{confidence:.2f}")
            return candidate

    if _apollo_configured():
        email, confidence = find_email_apollo(first_name, last_name, candidate.get("org"))
        if email:
            candidate["email"] = email
            candidate.setdefault("tags", []).append(f"email_confidence:{confidence:.2f}")
            return candidate

    if not (_hunter_configured() or _apollo_configured()):
        log.info(
            "enrich: neither HUNTER_API_KEY nor APOLLO_API_KEY configured, "
            "skipping email lookup for %r", candidate.get("name"),
        )
    return candidate


# ── dedup + suppression ──────────────────────────────────────────────────

def dedupe_candidates(candidates):
    """Within-batch dedup: primarily on lowercased email, falling back to
    (lowercased name, lowercased org) for candidates without an email yet."""
    seen_emails, seen_name_org, deduped = set(), set(), []
    for candidate in candidates:
        email = (candidate.get("email") or "").strip().lower()
        if email:
            if email in seen_emails:
                continue
            seen_emails.add(email)
            deduped.append(candidate)
            continue
        key = ((candidate.get("name") or "").strip().lower(), (candidate.get("org") or "").strip().lower())
        if key in seen_name_org:
            continue
        seen_name_org.add(key)
        deduped.append(candidate)
    return deduped


def is_suppressed(email):
    if not email:
        return False
    return SuppressedEmail.query.filter_by(email=email.strip().lower()).first() is not None


def suppress_email(email, reason="bounced"):
    """Idempotent insert into the suppression list."""
    email = (email or "").strip().lower()
    if not email:
        return None
    existing = SuppressedEmail.query.filter_by(email=email).first()
    if existing:
        return existing
    row = SuppressedEmail(email=email, reason=reason)
    db.session.add(row)
    db.session.commit()
    return row


# ── ingestion ─────────────────────────────────────────────────────────────

def ingest_candidates(candidates, enrich_emails=True, commit=True):
    """Turns a batch of candidate dicts into `Lead` rows. Returns a summary
    dict of counts. A candidate is skipped (not an error) when: it's a
    within-batch duplicate, its email is on the suppression list, a `Lead`
    with that email already exists, or no email could be resolved at all.
    """
    summary = {"created": 0, "skipped_duplicate": 0, "skipped_suppressed": 0, "skipped_no_email": 0}
    created_leads = []

    for candidate in dedupe_candidates(candidates):
        if enrich_emails:
            candidate = find_and_verify_email(candidate)

        email = (candidate.get("email") or "").strip().lower()
        if not email:
            summary["skipped_no_email"] += 1
            continue

        if is_suppressed(email):
            summary["skipped_suppressed"] += 1
            continue

        if Lead.query.filter_by(email=email).first():
            summary["skipped_duplicate"] += 1
            continue

        lead = Lead(
            name=candidate.get("name") or email,
            email=email,
            phone=candidate.get("phone"),
            track=candidate["track"],
            source=candidate.get("source", "outreach_builder"),
            org=candidate.get("org"),
            role=candidate.get("role"),
            state=candidate.get("state"),
            tags=candidate.get("tags") or [],
        )
        db.session.add(lead)
        created_leads.append(lead)
        summary["created"] += 1

    if commit and created_leads:
        db.session.commit()
    return summary
