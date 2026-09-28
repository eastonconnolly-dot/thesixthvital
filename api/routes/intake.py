"""Public, token-based onboarding forms (Phase 6): the intake form and, for
program-track cohorts, the sponsor roster + room/AV checklist. Both rows are
created by services/onboarding.py::trigger_onboarding(). Standalone
blueprint, not yet registered in api/app.py -- see api/INTEGRATION.md."""

from flask import Blueprint, abort, render_template, request

from extensions import db
from models import CohortRoster, IntakeForm, utcnow

bp = Blueprint("intake", __name__)

AV_CHECKLIST_OPTIONS = (
    ("projector", "Projector / screen"),
    ("whiteboard", "Whiteboard"),
    ("breakout_space", "Breakout space"),
    ("microphone", "Microphone / PA"),
    ("recording_setup", "Recording setup (camera + audio)"),
)


@bp.get("/intake/<token>")
def intake_form(token):
    intake = IntakeForm.query.filter_by(token=token).first()
    if not intake:
        abort(404)
    return render_template("intake/form.html", intake=intake, already_submitted=intake.submitted_at is not None)


@bp.post("/intake/<token>")
def intake_submit(token):
    intake = IntakeForm.query.filter_by(token=token).first()
    if not intake:
        abort(404)
    if intake.submitted_at is not None:
        return render_template(
            "intake/form.html", intake=intake, already_submitted=True,
            error="This intake form was already submitted.",
        ), 409

    responses = {
        "emergency_contact": (request.form.get("emergency_contact") or "").strip(),
        "sizing": (request.form.get("sizing") or "").strip(),
        "accessibility": (request.form.get("accessibility") or "").strip(),
        "special_requests": (request.form.get("special_requests") or "").strip(),
    }
    intake.responses = responses
    intake.submitted_at = utcnow()
    db.session.commit()
    return render_template("intake/form.html", intake=intake, already_submitted=True, just_submitted=True)


@bp.get("/roster/<token>")
def roster_form(token):
    roster = CohortRoster.query.filter_by(token=token).first()
    if not roster:
        abort(404)
    return render_template(
        "intake/roster.html", roster=roster, av_options=AV_CHECKLIST_OPTIONS,
        already_submitted=roster.submitted_at is not None,
    )


@bp.post("/roster/<token>")
def roster_submit(token):
    roster = CohortRoster.query.filter_by(token=token).first()
    if not roster:
        abort(404)
    if roster.submitted_at is not None:
        return render_template(
            "intake/roster.html", roster=roster, av_options=AV_CHECKLIST_OPTIONS,
            already_submitted=True, error="This roster was already submitted.",
        ), 409

    names = request.form.getlist("participant_name")
    emails = request.form.getlist("participant_email")
    participants = [
        {"name": name.strip(), "email": email.strip().lower()}
        for name, email in zip(names, emails)
        if name.strip() and email.strip()
    ]
    av_checklist = {key: (request.form.get(key) == "on") for key, _ in AV_CHECKLIST_OPTIONS}

    roster.participants = participants
    roster.av_checklist = av_checklist
    roster.submitted_at = utcnow()
    db.session.commit()
    return render_template(
        "intake/roster.html", roster=roster, av_options=AV_CHECKLIST_OPTIONS,
        already_submitted=True, just_submitted=True,
    )
