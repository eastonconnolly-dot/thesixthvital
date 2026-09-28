import secrets
from datetime import date

from flask import Blueprint, jsonify, request, send_file, current_app

from extensions import db
from models import Application, ConsentRequest, Deal, EncounterSession, Lead, Scorecard, TRACKS
from services.qualify import score_application
from services.pdf.badge import render_badge_pdf, render_badge_png
from services.pdf.scorecard import render_cohort_scorecard_pdf, render_scorecard_pdf

bp = Blueprint("public", __name__)


@bp.post("/apply")
def apply():
    data = request.get_json(silent=True) or {}

    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    track = (data.get("track") or "").strip()

    if not name or not email or track not in TRACKS:
        return jsonify({"error": "name, email, and a valid track are required"}), 400

    org = (data.get("org") or "").strip() or None
    lead = Lead(
        name=name,
        email=email,
        phone=(data.get("phone") or "").strip() or None,
        track=track,
        source="site_apply",
        org=org,
        role=(data.get("role") or "").strip() or None,
        state=(data.get("state") or "").strip().upper()[:2] or None,
    )
    db.session.add(lead)
    db.session.flush()

    answers = {
        "budget_ok": data.get("budget_ok"),
        "timeline": data.get("timeline"),
        "situation": data.get("situation"),
    }
    score, qualified, budget_ok = score_application(track, org, answers)

    application = Application(
        lead_id=lead.id, answers=answers, budget_ok=budget_ok, score=score, qualified=qualified,
    )
    db.session.add(application)

    lead.status = "qualified" if qualified else "nurture"
    if qualified:
        lead.qualifier_token = secrets.token_urlsafe(32)
    db.session.commit()

    # Booking-link / nurture email sends happen from the outreach engine (Phase 2)
    # once Gmail credentials are configured — kept out of the request path here
    # so `/apply` never blocks on an external API call.

    return jsonify({
        "lead_id": lead.id,
        "application_id": application.id,
        "qualified": qualified,
        "qualifier_token": lead.qualifier_token,
    }), 201


@bp.get("/public/proof")
def public_proof():
    scorecards = Scorecard.query.filter(Scorecard.lift.isnot(None)).all()
    lifts = [sc.lift["total_lift"] for sc in scorecards if sc.lift]
    avg_lift = round(sum(lifts) / len(lifts), 1) if lifts else None

    sessions_count = EncounterSession.query.count()
    participants_count = Scorecard.query.count()
    programs_count = db.session.query(Deal.lead_id).join(Lead).filter(Lead.track == "program").distinct().count()

    stats = [
        {"value": f"{avg_lift:+.1f}" if avg_lift is not None else "—", "label": "Average encounter-score lift"},
        {"value": str(sessions_count), "label": "Sessions delivered"},
        {"value": str(participants_count), "label": "Participants scored"},
        {"value": str(programs_count), "label": "Programs enrolled"},
    ]
    approved = (
        ConsentRequest.query
        .filter_by(kind="testimonial", granted=True, approved=True)
        .filter(ConsentRequest.response_text.isnot(None))
        .order_by(ConsentRequest.responded_at.desc())
        .limit(12)
        .all()
    )
    testimonials = [
        {"quote": c.response_text, "attribution": c.participant_name or "RPSAS participant"}
        for c in approved
    ]
    return jsonify({"stats": stats, "testimonials": testimonials})


@bp.get("/badge/<int:participant_id>.pdf")
def badge_pdf(participant_id):
    lead = Lead.query.get_or_404(participant_id)
    buf = render_badge_pdf(participant_name=lead.name)
    return send_file(buf, mimetype="application/pdf", download_name=f"rpsas-badge-{participant_id}.pdf")


@bp.get("/badge/<int:participant_id>.png")
def badge_png(participant_id):
    lead = Lead.query.get_or_404(participant_id)
    buf = render_badge_png(participant_name=lead.name)
    return send_file(buf, mimetype="image/png", download_name=f"rpsas-badge-{participant_id}.png")


@bp.get("/scorecards/<int:session_id>.pdf")
def scorecard_pdf(session_id):
    session = EncounterSession.query.get_or_404(session_id)
    scorecards = Scorecard.query.filter_by(session_id=session_id).all()
    address = current_app.config["COMPANY_MAILING_ADDRESS"]

    if len(scorecards) == 1:
        sc = scorecards[0]
        buf = render_scorecard_pdf(
            session_label=session.type or f"Session {session_id}",
            participant_name=sc.participant_name,
            baseline_scores=sc.baseline,
            final_scores=sc.final,
            clip_timestamps=sc.clip_timestamps,
            mailing_address=address,
        )
        return send_file(buf, mimetype="application/pdf", download_name=f"scorecard-{session_id}.pdf")

    pairs = [(sc.participant_name, sc.baseline, sc.final) for sc in scorecards if sc.baseline and sc.final]
    buf = render_cohort_scorecard_pdf(
        session_label=session.type or f"Session {session_id}",
        participant_pairs=pairs,
        mailing_address=address,
    )
    return send_file(buf, mimetype="application/pdf", download_name=f"cohort-scorecard-{session_id}.pdf")
