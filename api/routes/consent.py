"""Public routes for post-delivery follow-ups (Phase 6):
  - GET/POST /consent/<token> -- testimonial / clip-consent response
  - GET /refer/<lead_id> -- the day-7 referral ask's shareable link

Registered in api/app.py. A positive response lands with approved=False;
routes/delivery.py::approve_testimonial is the founder-approval gate
(surfaced on that deal's /admin/delivery/<id> page), and
routes/public.py::public_proof already queries approved=True rows for the
public Proof page."""

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
