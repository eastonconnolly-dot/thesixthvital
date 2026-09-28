"""Thin SignWell client. SignWell doesn't exist anywhere in the Hub (see
REUSE.md) — this integrates with SignWell's real API directly.

NOTE: endpoint shape below follows SignWell's documented v1 REST API
(https://www.signwell.com/api/) as of this writing — verify against current
docs before relying on it in production, since this hasn't been exercised
against a live account/key.

In test mode (default, or automatically when no API key is configured) this
returns stub data so the rest of the app is exercisable without a live
SignWell account.
"""

import base64

import requests
from flask import current_app

API_BASE = "https://www.signwell.com/api/v1"


def _test_mode():
    cfg = current_app.config
    return cfg["SIGNWELL_TEST_MODE"] or not cfg["SIGNWELL_API_KEY"]


def _headers():
    return {
        "X-Api-Key": current_app.config["SIGNWELL_API_KEY"],
        "Content-Type": "application/json",
    }


def create_envelope(pdf_bytes, filename, signer_name, signer_email, deal_id, test_mode_override=None):
    """Creates a single-document, single-signer envelope. Returns
    {"envelope_id": str, "sign_url": str|None, "test_mode": bool}."""
    test_mode = _test_mode() if test_mode_override is None else test_mode_override

    if test_mode:
        return {
            "envelope_id": f"test-envelope-deal-{deal_id}",
            "sign_url": f"https://www.signwell.com/sandbox/sign/test-envelope-deal-{deal_id}",
            "test_mode": True,
        }

    payload = {
        "test_mode": False,
        "draft": False,
        "files": [{
            "name": filename,
            "file_base64": base64.b64encode(pdf_bytes).decode("ascii"),
        }],
        "recipients": [{
            "id": "1",
            "name": signer_name,
            "email": signer_email,
        }],
        "metadata": {"deal_id": str(deal_id)},
    }
    resp = requests.post(f"{API_BASE}/documents/", json=payload, headers=_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    sign_url = None
    for recipient in data.get("recipients", []):
        if recipient.get("id") == "1":
            sign_url = recipient.get("embedded_signing_url") or recipient.get("signing_url")
    return {"envelope_id": data.get("id"), "sign_url": sign_url, "test_mode": False}


def get_envelope_status(envelope_id):
    if envelope_id.startswith("test-envelope-"):
        return {"status": "sent", "test_mode": True}
    resp = requests.get(f"{API_BASE}/documents/{envelope_id}", headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def verify_webhook_signature(payload, signature_header):
    """Verifies a SignWell webhook via HMAC-SHA256 over the raw body using
    SIGNWELL_WEBHOOK_SECRET. SignWell's exact header name/scheme should be
    confirmed against current docs before go-live — this assumes the common
    "hex HMAC-SHA256 of the raw body" pattern used by most webhook providers.
    In test mode (no secret configured), verification is skipped and every
    call is treated as valid — fine for sandbox testing, not for production."""
    import hashlib
    import hmac

    secret = current_app.config["SIGNWELL_WEBHOOK_SECRET"]
    if not secret:
        return current_app.config["SIGNWELL_TEST_MODE"]
    if not signature_header:
        return False
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)
