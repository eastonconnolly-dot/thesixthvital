"""Phase 6 pre-call qualifier — public (lead-facing, no admin auth), the
step between a qualified application and a founder call. See
api/INTEGRATION.md for the one-line registration this needs in
api/app.py (not edited here — another engineer is actively working in/
around that file)."""

from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, render_template, request

from extensions import db
from models import Deal, Lead, QualifierSession
from services import calendar_client, qualifier_chat, stripe_client

bp = Blueprint("qualifier", __name__, url_prefix="/qualify")


def _latest_session(lead_id, status=None):
    q = QualifierSession.query.filter_by(lead_id=lead_id)
    if status:
        q = q.filter_by(status=status)
    return q.order_by(QualifierSession.id.desc()).first()


def _fit_confirmed(lead_id):
    """True only if this lead has a *completed* qualifier session that
    verdicted budget_fit=True. This is the sole gate on the booking routes
    below — the founder must never see a call under $10k."""
    session = _latest_session(lead_id, status="completed")
    return bool(session and session.budget_fit is True), session


@bp.get("/<int:lead_id>")
def chat_page(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    return render_template("qualifier/chat.html", lead=lead)


@bp.post("/<int:lead_id>/start")
def start(lead_id):
    lead = Lead.query.get_or_404(lead_id)

    existing = _latest_session(lead_id, status="in_progress")
    if existing:
        last_question = next((t["text"] for t in reversed(existing.transcript) if t["role"] == "assistant"), None)
        return jsonify({"session_id": existing.id, "question": last_question})

    opening = qualifier_chat.start_qualifier(lead)
    session = QualifierSession(
        lead_id=lead.id,
        transcript=[{"role": "assistant", "text": opening}],
        status="in_progress",
    )
    db.session.add(session)
    db.session.commit()
    return jsonify({"session_id": session.id, "question": opening}), 201


@bp.post("/<int:lead_id>/turn")
def turn(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    session = _latest_session(lead_id, status="in_progress")
    if not session:
        return jsonify({"error": "no in-progress qualifier session — call /start first"}), 404

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    transcript = list(session.transcript)
    transcript.append({"role": "lead", "text": text})

    reply = qualifier_chat.qualifier_reply(lead, transcript)
    transcript.append({"role": "assistant", "text": reply})

    session.transcript = transcript
    db.session.commit()

    return jsonify({"reply": reply})


@bp.post("/<int:lead_id>/complete")
def complete(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    session = _latest_session(lead_id, status="in_progress")
    if not session:
        return jsonify({"error": "no in-progress qualifier session — call /start first"}), 404

    result = qualifier_chat.complete_qualifier(session)
    session.brief = result["brief"]
    session.budget_fit = result["budget_fit_10k_plus"]
    session.recommended_package = result["recommended_package"]
    session.status = "completed"
    session.completed_at = datetime.now(timezone.utc)

    if session.budget_fit:
        # $10k+ fit: open (or reuse) a Deal at "discovery" and let the
        # frontend move on to booking. Package/amount are still unknown at
        # this point — the founder's own discovery call determines those,
        # then Phase 6's call-to-proposal (services/call_to_proposal.py)
        # fills them in for real.
        deal = (
            Deal.query.filter_by(lead_id=lead.id)
            .filter(Deal.stage != "closed_lost")
            .order_by(Deal.created.desc())
            .first()
        )
        if not deal:
            deal = Deal(lead_id=lead.id, package="tbd", amount_cents=0, balance_due_cents=0, stage="discovery")
            db.session.add(deal)
        deal.qualifier_brief = session.brief
        db.session.commit()

        return jsonify({
            "budget_fit": True,
            "deal_id": deal.id,
            "brief": session.brief,
            "next": "book_a_call",
        })

    db.session.commit()

    package_key = session.recommended_package
    success_url = f"{current_app.config['SITE_BASE_URL']}/apply.html?checkout=paid"
    cancel_url = f"{current_app.config['SITE_BASE_URL']}/apply.html?checkout=cancelled"
    checkout = stripe_client.create_package_checkout_session(package_key, lead, success_url, cancel_url)

    return jsonify({
        "budget_fit": False,
        "recommended_package": package_key,
        "checkout_url": checkout["url"],
    })


@bp.get("/<int:lead_id>/slots")
def slots(lead_id):
    Lead.query.get_or_404(lead_id)
    fit, _ = _fit_confirmed(lead_id)
    if not fit:
        # The founder never takes a call under $10k -- no booking path for
        # anyone who hasn't cleared the qualifier with a True verdict.
        return jsonify({"error": "not eligible to book a call"}), 403

    try:
        available = calendar_client.available_slots()
    except Exception as exc:
        current_app.logger.warning("qualifier: failed to fetch slots for lead %s: %s", lead_id, exc)
        return jsonify({"error": "could not load available times — check Google Calendar credentials"}), 502

    return jsonify({"slots": [s.isoformat() for s in available]})


@bp.post("/<int:lead_id>/book")
def book(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    fit, _ = _fit_confirmed(lead_id)
    if not fit:
        return jsonify({"error": "not eligible to book a call"}), 403

    data = request.get_json(silent=True) or {}
    slot_start_raw = data.get("slot_start")
    if not slot_start_raw:
        return jsonify({"error": "slot_start is required"}), 400
    try:
        slot_start = datetime.fromisoformat(slot_start_raw)
    except ValueError:
        return jsonify({"error": "slot_start must be an ISO 8601 datetime"}), 400

    try:
        event = calendar_client.book_slot(slot_start, summary=f"RPSAS discovery call — {lead.name}", attendee_email=lead.email)
    except Exception as exc:
        current_app.logger.warning("qualifier: failed to book call for lead %s: %s", lead.id, exc)
        return jsonify({"error": "could not book the call — check Google Calendar credentials"}), 502

    lead.status = "booked"
    db.session.commit()

    return jsonify({"booked": True, "slot_start": slot_start.isoformat(), "event_id": event.get("id")})
