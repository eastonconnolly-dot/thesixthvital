"""Public routes for post-delivery follow-ups (Phase 6):
  - GET/POST /consent/<token> -- testimonial / clip-consent response
  - GET /refer/<lead_id> -- the day-7 referral ask's shareable link

Standalone blueprint, not yet registered in api/app.py -- see
api/INTEGRATION.md. A positive, founder-approved testimonial is what's
meant to flow to the public Proof page -- see api/INTEGRATION.md for the
small routes/public.py addition needed to actually query these (out of
scope to edit that file directly here)."""

from flask import Blueprint, abort, render_template, request

from extensions import db
from models import ConsentRequest, Lead, ReferralClick, utcnow

bp = Blueprint("consent", __name__)


@bp.get("/consent/<token>")
def consent_form(token):
    consent = ConsentRequest.query.filter_by(token=token).first()
    if not consent:
        abort(404)
    return render_template("consent/respond.html", consent=consent, already_responded=consent.responded_at is not None)


@bp.post("/consent/<token>")
def consent_submit(token):
    consent = ConsentRequest.query.filter_by(token=token).first()
    if not consent:
        abort(404)
    if consent.responded_at is not None:
        return render_template(
            "consent/respond.html", consent=consent, already_responded=True,
            error="This request was already answered.",
        ), 409

    granted = request.form.get("granted") == "yes"
    response_text = (request.form.get("response_text") or "").strip()

    consent.granted = granted
    consent.response_text = response_text or None
    consent.responded_at = utcnow()
    db.session.commit()

    return render_template("consent/respond.html", consent=consent, already_responded=True, just_responded=True)


@bp.get("/refer/<int:lead_id>")
def refer(lead_id):
    lead = db.session.get(Lead, lead_id)
    if not lead:
        abort(404)
    db.session.add(ReferralClick(lead_id=lead.id))
    db.session.commit()
    return render_template("consent/refer.html", lead=lead)
