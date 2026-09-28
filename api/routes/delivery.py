"""Post-delivery automation admin UI (Phase 6): "founder taps session
complete and uploads the reps." Business logic lives in
services/delivery.py; this blueprint is just form-handling + the
testimonial-approval action.

Standalone blueprint, not yet registered in api/app.py -- see
api/INTEGRATION.md."""

from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for

from extensions import db
from models import ConsentRequest, Deal
from routes.admin import admin_required
from services.delivery import complete_session
from shared.rubric import DIMENSIONS, InvalidScoreError

bp = Blueprint("delivery", __name__, url_prefix="/admin/delivery")


@bp.get("/<int:deal_id>")
@admin_required
def form(deal_id):
    deal = Deal.query.get_or_404(deal_id)
    session_ids = [s.id for s in deal.sessions]
    testimonials = (
        ConsentRequest.query
        .filter(ConsentRequest.kind == "testimonial")
        .filter(ConsentRequest.session_id.in_(session_ids))
        .all()
        if session_ids else []
    )
    return render_template(
        "admin/delivery.html", deal=deal, lead=deal.lead, dimensions=DIMENSIONS,
        testimonials=testimonials,
    )


@bp.post("/<int:deal_id>/complete")
@admin_required
def complete(deal_id):
    deal = Deal.query.get_or_404(deal_id)
    if deal.delivered_at is not None:
        flash("This deal was already marked delivered.", "error")
        return redirect(url_for("delivery.form", deal_id=deal.id))

    session_date_raw = request.form.get("session_date") or ""
    session_type = (request.form.get("session_type") or "").strip() or "intensive"
    try:
        session_date = datetime.strptime(session_date_raw, "%Y-%m-%d").date() if session_date_raw else None
    except ValueError:
        flash("Invalid session date.", "error")
        return redirect(url_for("delivery.form", deal_id=deal.id))

    names = request.form.getlist("participant_name")
    emails = request.form.getlist("participant_email")

    participants = []
    for i, (name, email) in enumerate(zip(names, emails)):
        name = name.strip()
        email = email.strip().lower()
        if not name or not email:
            continue
        try:
            baseline = {dim: int(request.form.get(f"baseline_{dim}_{i}", "")) for dim in DIMENSIONS}
            final = {dim: int(request.form.get(f"final_{dim}_{i}", "")) for dim in DIMENSIONS}
        except (TypeError, ValueError):
            flash(f"Missing or invalid scores for {name or 'a participant'}.", "error")
            return redirect(url_for("delivery.form", deal_id=deal.id))
        participants.append({"name": name, "email": email, "baseline": baseline, "final": final})

    if not participants:
        flash("Add at least one participant with baseline and final scores.", "error")
        return redirect(url_for("delivery.form", deal_id=deal.id))

    try:
        result = complete_session(deal, session_date, session_type, participants)
    except InvalidScoreError as e:
        flash(f"Invalid scores: {e}", "error")
        return redirect(url_for("delivery.form", deal_id=deal.id))

    flash(
        f"Session marked complete. {result['scorecards_sent']} scorecard(s) sent, "
        f"{result['practice_seats_provisioned']} Practice seat(s) provisioned.",
        "success",
    )
    return redirect(url_for("delivery.form", deal_id=deal.id))


@bp.post("/testimonials/<int:consent_id>/approve")
@admin_required
def approve_testimonial(consent_id):
    consent = ConsentRequest.query.get_or_404(consent_id)
    if consent.kind != "testimonial":
        flash("Only testimonial responses can be approved.", "error")
        return redirect(request.referrer or url_for("admin.dashboard"))
    if not consent.granted:
        flash("This participant did not grant permission -- can't approve for the Proof page.", "error")
        return redirect(request.referrer or url_for("admin.dashboard"))

    consent.approved = True
    db.session.commit()
    flash("Testimonial approved for the Proof page.", "success")
    return redirect(request.referrer or url_for("admin.dashboard"))
