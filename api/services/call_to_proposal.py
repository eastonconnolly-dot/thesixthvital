"""Phase 6 call-to-proposal: one Claude call extracts package/date/
participants/special-terms from a discovery-call transcript, then reuses
the exact same proposal-PDF + native-e-sign + Stripe-deposit-link pipeline
`routes/admin.py::generate_proposal()` already runs today — the only
difference is the extracted fields drive it instead of a founder-filled
form, and the result sits behind `Deal.proposal_pending_review` until the
founder approves it (or the 2-hour `flask auto-approve-proposals` sweep
does, per api/INTEGRATION.md).

Recording/transcription infrastructure (pulling audio from Google Meet or
Quo) is explicitly out of scope — this takes plain transcript text, assumed
already available."""

import json
from datetime import date, datetime, timedelta, timezone

from flask import current_app

from extensions import db
from models import PACKAGES, Deal
from services import claude_client, esign, stripe_client
from services.pdf.proposal import render_proposal_pdf

MAX_TOKENS_EXTRACT = 900

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "package": {"type": "string", "enum": list(PACKAGES.keys())},
        # ISO date (YYYY-MM-DD) or "" when the call didn't land on one --
        # empty-string sentinel per the same convention as
        # patient_sim.SCORE_SCHEMA's named_p_after_shift (a ["string","null"]
        # type union + null in an enum 400s against Claude's structured-
        # output validator).
        "delivery_date": {"type": "string"},
        "participants": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "email": {"type": "string"},
                },
                "required": ["name", "email"],
                "additionalProperties": False,
            },
        },
        "special_terms": {"type": "string"},
    },
    "required": ["package", "delivery_date", "participants", "special_terms"],
    "additionalProperties": False,
}


def extract_call_details(transcript_text):
    """One JSON-schema-constrained Claude call. Returns
    {"package": str, "delivery_date": date|None, "participants": [...],
    "special_terms": str}."""
    package_options = ", ".join(f"{key} ({pkg['label']})" for key, pkg in PACKAGES.items())
    prompt = (
        "You are reading the transcript of a discovery call between RPSAS's founder and a "
        "prospective client. Extract exactly what was agreed: which package they're buying "
        f"(one of: {package_options}), the "
        "delivery date they landed on (empty string if none was pinned down), everyone who "
        "will participate (name + email, best guess at email if only spoken aloud and "
        "clearly stated — omit anyone whose email truly wasn't mentioned), and any special "
        "terms or promises made on the call (payment schedule tweaks, extra deliverables, "
        "scheduling constraints) as free text — empty string if none.\n\n"
        f"TRANSCRIPT:\n{transcript_text}"
    )
    resp = claude_client.client().messages.create(
        model=claude_client.model(),
        max_tokens=MAX_TOKENS_EXTRACT,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": EXTRACT_SCHEMA}},
    )
    data = json.loads(_text(resp))

    delivery_date = None
    if data.get("delivery_date"):
        try:
            delivery_date = datetime.strptime(data["delivery_date"], "%Y-%m-%d").date()
        except ValueError:
            delivery_date = None

    return {
        "package": data["package"],
        "delivery_date": delivery_date,
        "participants": data.get("participants") or [],
        "special_terms": data.get("special_terms") or "",
    }


def generate_proposal_from_call(deal, transcript_text):
    """Extracts call details, fills in `deal`, and generates the proposal
    PDF + e-sign request + Stripe deposit checkout — exactly what
    admin.py's generate_proposal() does, minus activating it. Sets
    `deal.proposal_pending_review = True` instead of `stage = "proposal_sent"`;
    routes/call_intake.py::approve() (or the auto-approve sweep below) is
    the only thing that flips the stage. Returns the extracted-details dict
    for the review page."""
    lead = deal.lead
    extracted = extract_call_details(transcript_text)

    package_key = extracted["package"]
    package = PACKAGES[package_key]
    package_label = package["label"]

    deal.package = package_key
    deal.amount_cents = package["amount_cents"]
    deal.balance_due_cents = package["amount_cents"] - round(package["amount_cents"] * 0.5)
    if extracted["delivery_date"]:
        deal.delivery_date = extracted["delivery_date"]

    deliverables = [
        f"{package_label} intensive",
        "Baseline and final encounter scoring on the 5-dimension rubric",
        "A scorecard report within one week of delivery",
    ]
    if extracted["participants"]:
        names = ", ".join(p["name"] for p in extracted["participants"] if p.get("name"))
        if names:
            deliverables.append(f"Participants: {names}")
    if extracted["special_terms"]:
        deliverables.append(f"Special terms: {extracted['special_terms']}")

    success_url = f"{current_app.config['SITE_BASE_URL']}/apply.html?deposit=paid"
    cancel_url = f"{current_app.config['SITE_BASE_URL']}/apply.html?deposit=cancelled"
    checkout = stripe_client.create_deposit_checkout_session(deal, package_label, success_url, cancel_url)
    deal.deposit_stripe_session_id = checkout["id"]

    deal.proposal_context = {
        "lead_name": lead.name,
        "lead_org": lead.org,
        "package_label": package_label,
        "deliverables": deliverables,
        "deposit_link": checkout["url"],
        "delivery_date": deal.delivery_date.isoformat() if deal.delivery_date else None,
        "mailing_address": current_app.config["COMPANY_MAILING_ADDRESS"],
        "participants": extracted["participants"],
        "special_terms": extracted["special_terms"],
        "source": "call_to_proposal",
    }

    pdf_buf = render_proposal_pdf(
        deal={"amount_cents": deal.amount_cents, "delivery_date": deal.proposal_context["delivery_date"]},
        lead={"name": lead.name, "org": lead.org},
        package_label=package_label,
        deliverables=deliverables,
        deposit_link=checkout["url"],
        mailing_address=current_app.config["COMPANY_MAILING_ADDRESS"],
    )
    deal.proposal_pdf_data = pdf_buf.getvalue()

    sig_request, sign_url = esign.create_signature_request(deal, signer_name=lead.name, signer_email=lead.email)

    deal.proposal_pending_review = True
    deal.proposal_pending_since = datetime.now(timezone.utc)

    db.session.commit()

    return {**extracted, "package_label": package_label, "sign_url": sign_url, "deposit_link": checkout["url"]}


def approve_proposal(deal):
    """The founder's one-click approval (or the auto-approve sweep) —
    activates a pending proposal exactly the way admin.py's
    generate_proposal() does at the end: flips the stage, clears the
    pending-review flag."""
    if not deal.proposal_pending_review:
        return False
    deal.stage = "proposal_sent"
    deal.proposal_pending_review = False
    deal.proposal_pending_since = None
    db.session.commit()
    return True


def sweep_auto_approve(threshold_hours=2, now=None):
    """Finds every Deal still `proposal_pending_review` after
    `threshold_hours`, and approves it — the "auto-send if not touched in 2
    hours" behavior from the brief. Called by `flask auto-approve-proposals`
    (api/app.py), scheduled via render.yaml cron (see api/INTEGRATION.md).
    Returns the list of approved deal ids. Pure w.r.t. wall-clock time via
    the `now` param so tests don't need to sleep."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=threshold_hours)

    stale = Deal.query.filter(
        Deal.proposal_pending_review.is_(True),
        Deal.proposal_pending_since.isnot(None),
        Deal.proposal_pending_since <= cutoff,
    ).all()

    approved_ids = []
    for deal in stale:
        if approve_proposal(deal):
            approved_ids.append(deal.id)
    return approved_ids


def _text(resp):
    return next(b.text for b in resp.content if b.type == "text").strip()
