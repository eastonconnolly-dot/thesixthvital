"""Post-delivery automation (Phase 6): everything that should fire the
moment the founder marks a session complete and enters baseline/final
scores, with no further founder action required.

`complete_session()` is called by routes/delivery.py's
`POST /admin/delivery/<deal_id>/complete` handler. `send_scheduled_followups()`
is the CLI-swept function for the 30-day check-in and day-7 referral ask
(`flask send-scheduled-followups`, registered in api/app.py).
"""

import secrets
from datetime import timedelta

from flask import current_app

from extensions import db
from models import ConsentRequest, EncounterSession, Lead, ScheduledFollowup, Scorecard, utcnow
from services import gmail_client, magic_link
from services.pdf.scorecard import render_cohort_scorecard_pdf, render_scorecard_pdf
from shared.rubric import MAX_TOTAL, score_lift

CHECKIN_DELAY_DAYS = 30
REFERRAL_DELAY_DAYS = 7


def _token():
    return secrets.token_urlsafe(24)


def _unsubscribe_url(lead_id):
    return f"{current_app.config['API_BASE_URL']}/unsubscribe?lead_id={lead_id}"


def complete_session(deal, session_date, session_type, participants):
    """`participants`: list of dicts {"name", "email", "baseline": {...5
    dims...}, "final": {...5 dims...}}. Idempotency: guarded by
    `Deal.delivered_at` -- the route checks this before calling in, same
    pattern as services/onboarding.py::trigger_onboarding's
    onboarding_triggered_at guard.

    Returns a summary dict."""
    if deal.delivered_at is not None:
        return {"status": "already_delivered"}

    mailing_address = current_app.config["COMPANY_MAILING_ADDRESS"]
    brand = current_app.config["BRAND_NAME"]

    session = EncounterSession(
        deal_id=deal.id, date=session_date, type=session_type,
        participants=[{"name": p["name"], "email": p["email"]} for p in participants],
    )
    db.session.add(session)
    db.session.flush()

    scorecards = []
    for p in participants:
        lift = score_lift(p["baseline"], p["final"])
        sc = Scorecard(
            session_id=session.id, participant_name=p["name"], participant_email=p["email"],
            baseline=p["baseline"], final=p["final"], lift=lift, clip_timestamps=p.get("clip_timestamps") or [],
        )
        db.session.add(sc)
        scorecards.append(sc)
    db.session.flush()

    result = {
        "status": "ok", "session_id": session.id, "scorecards_sent": 0, "cohort_sent": False,
        "consent_requests_created": 0, "practice_seats_provisioned": 0, "followups_scheduled": 0,
        "program_deck_sent": False,
    }

    for sc in scorecards:
        # Provision the Practice seat FIRST so every other email for this
        # participant (scorecard, testimonial/consent asks) can carry a real
        # lead_id in its CAN-SPAM unsubscribe link, same shape every other
        # send in this app uses (routes/unsubscribe.py is lead_id-keyed only
        # and out of scope to change here -- see api/INTEGRATION.md).
        lead = _provision_practice_seat(deal, sc)
        if lead:
            result["practice_seats_provisioned"] += 1
            _schedule_followups(deal, lead)
            result["followups_scheduled"] += 2

        _send_scorecard_email(session, sc, lead, mailing_address, brand)
        result["scorecards_sent"] += 1
        _create_and_send_consent_requests(sc, lead)
        result["consent_requests_created"] += 2

    if len(scorecards) > 1:
        _send_cohort_email(deal, session, scorecards, mailing_address, brand, is_program_deck=False)
        result["cohort_sent"] = True

        if deal.lead.track == "program":
            _send_cohort_email(deal, session, scorecards, mailing_address, brand, is_program_deck=True)
            result["program_deck_sent"] = True

    deal.stage = "delivered"
    deal.delivered_at = utcnow()
    db.session.commit()
    return result


def _provision_practice_seat(deal, scorecard):
    if not scorecard.participant_email:
        return None
    try:
        sign_in_url, user = magic_link.request_login(
            scorecard.participant_email, track=deal.lead.track, name=scorecard.participant_name,
        )
    except Exception as exc:
        current_app.logger.warning("delivery: practice seat provisioning failed for scorecard=%s: %s", scorecard.id, exc)
        return None

    brand = current_app.config["BRAND_NAME"]
    first_name = (scorecard.participant_name or "").split(" ")[0] or scorecard.participant_name
    gmail_client.send_email(
        to_email=scorecard.participant_email,
        subject=f"Your {brand} Practice seat is ready",
        html_body=(
            f"<p>Hi {first_name},</p>"
            f"<p>Keep sharpening the skills from your session -- your "
            f"{brand} Practice seat is ready (7-day free trial included).</p>"
            f"<p><a href=\"{sign_in_url}\">Sign in to Practice</a></p>"
        ),
        unsubscribe_url=_unsubscribe_url(user.lead_id),
    )
    # PracticeUser.lead_id has no ORM relationship declared on either side
    # (see models.py) -- look the Lead up directly rather than assuming one.
    return db.session.get(Lead, user.lead_id) if user.lead_id else None


def _send_scorecard_email(session, scorecard, lead, mailing_address, brand):
    if not scorecard.participant_email:
        return None
    buf = render_scorecard_pdf(
        session_label=session.type or f"Session {session.id}",
        participant_name=scorecard.participant_name,
        baseline_scores=scorecard.baseline,
        final_scores=scorecard.final,
        clip_timestamps=scorecard.clip_timestamps,
        mailing_address=mailing_address,
    )
    first_name = (scorecard.participant_name or "").split(" ")[0] or scorecard.participant_name
    lift = scorecard.lift or {}
    html_body = (
        f"<p>Hi {first_name},</p>"
        f"<p>Your {brand} encounter scorecard is attached. Total lift: "
        f"{lift.get('total_lift', 0):+d} points "
        f"({lift.get('baseline_total', 0)} &rarr; {lift.get('final_total', 0)} out of {MAX_TOTAL}).</p>"
        f"<p>Congratulations on the work you put in.</p>"
    )
    unsubscribe_url = _unsubscribe_url(lead.id) if lead else f"{current_app.config['API_BASE_URL']}/"
    return gmail_client.send_email(
        to_email=scorecard.participant_email,
        subject=f"Your {brand} encounter scorecard",
        html_body=html_body,
        unsubscribe_url=unsubscribe_url,
        attachments=[(f"scorecard-{scorecard.id}.pdf", buf.getvalue(), "application/pdf")],
    )


def _send_cohort_email(deal, session, scorecards, mailing_address, brand, is_program_deck):
    pairs = [(sc.participant_name, sc.baseline, sc.final) for sc in scorecards if sc.baseline and sc.final]
    buf = render_cohort_scorecard_pdf(
        session_label=session.type or f"Session {session.id}",
        participant_pairs=pairs,
        mailing_address=mailing_address,
    )
    lead = deal.lead
    if is_program_deck:
        subject = f"{brand} program score report & leadership deck"
        note = (
            "<p>Attached: the full cohort score report. This doubles as the "
            "leadership deck summary -- a fuller narrative deck is prepared "
            "by the founder on request.</p>"
        )
    else:
        subject = f"{brand} cohort score report"
        note = "<p>Attached: the cohort score report for this session.</p>"

    html_body = f"<p>Hi {lead.name.split(' ')[0] if lead.name else 'there'},</p>{note}"
    return gmail_client.send_email(
        to_email=lead.email,
        subject=subject,
        html_body=html_body,
        unsubscribe_url=_unsubscribe_url(lead.id),
        attachments=[(f"cohort-scorecard-{session.id}.pdf", buf.getvalue(), "application/pdf")],
    )


def _create_and_send_consent_requests(scorecard, lead):
    if not scorecard.participant_email:
        return
    first_name = (scorecard.participant_name or "").split(" ")[0] or scorecard.participant_name
    brand = current_app.config["BRAND_NAME"]
    unsubscribe_url = _unsubscribe_url(lead.id) if lead else f"{current_app.config['API_BASE_URL']}/"

    asks = (
        ("testimonial", f"Would you share a testimonial for {brand}?",
         "We'd love a couple of sentences about your experience -- with your "
         "permission, we may feature it on our Proof page."),
        ("clip_consent", f"Can we use a clip from your session?",
         f"We'd like your permission to use a short clip from your encounter "
         f"recording in {brand}'s marketing."),
    )
    for kind, subject, ask in asks:
        cr = ConsentRequest(
            scorecard_id=scorecard.id, session_id=scorecard.session_id, kind=kind,
            participant_name=scorecard.participant_name, participant_email=scorecard.participant_email,
            token=_token(),
        )
        db.session.add(cr)
        db.session.flush()
        respond_url = f"{current_app.config['API_BASE_URL']}/consent/{cr.token}"
        gmail_client.send_email(
            to_email=scorecard.participant_email,
            subject=subject,
            html_body=f"<p>Hi {first_name},</p><p>{ask}</p><p><a href=\"{respond_url}\">Respond here</a></p>",
            unsubscribe_url=unsubscribe_url,
        )


def _schedule_followups(deal, lead):
    now = utcnow()
    db.session.add(ScheduledFollowup(
        deal_id=deal.id, lead_id=lead.id, kind="checkin_30day", due_at=now + timedelta(days=CHECKIN_DELAY_DAYS),
    ))
    db.session.add(ScheduledFollowup(
        deal_id=deal.id, lead_id=lead.id, kind="referral_ask", due_at=now + timedelta(days=REFERRAL_DELAY_DAYS),
    ))


# ── CLI sweep: flask send-scheduled-followups ────────────────────────────

def send_scheduled_followups(now=None):
    """Sends whatever's due: the 30-day check-in confirmation and the
    day-7 referral ask. Safe to call repeatedly -- each send commits
    sent_at immediately, so a due row is only ever sent once."""
    now = now or utcnow()
    summary = {"checkin_sent": 0, "referral_sent": 0, "errors": 0}

    due = (
        ScheduledFollowup.query
        .filter(ScheduledFollowup.sent_at.is_(None))
        .filter(ScheduledFollowup.due_at <= now)
        .all()
    )
    for followup in due:
        lead = followup.lead
        if not lead or not lead.email:
            followup.sent_at = now
            db.session.commit()
            continue
        try:
            if followup.kind == "checkin_30day":
                _send_checkin_confirmation(lead)
                summary["checkin_sent"] += 1
            elif followup.kind == "referral_ask":
                _send_referral_ask(lead)
                summary["referral_sent"] += 1
            followup.sent_at = now
            db.session.commit()
        except Exception as exc:
            current_app.logger.warning("delivery: scheduled followup send failed for id=%s: %s", followup.id, exc)
            summary["errors"] += 1

    return summary


def _send_checkin_confirmation(lead):
    brand = current_app.config["BRAND_NAME"]
    first_name = (lead.name or "").split(" ")[0] or lead.name
    html_body = (
        f"<p>Hi {first_name},</p>"
        f"<p>It's been 30 days since your {brand} session -- your check-in "
        f"is confirmed. We'll be in touch to schedule it if we haven't "
        f"already connected.</p>"
    )
    return gmail_client.send_email(
        to_email=lead.email, subject=f"Your {brand} 30-day check-in",
        html_body=html_body, unsubscribe_url=_unsubscribe_url(lead.id),
    )


def _send_referral_ask(lead):
    brand = current_app.config["BRAND_NAME"]
    first_name = (lead.name or "").split(" ")[0] or lead.name
    referral_url = f"{current_app.config['API_BASE_URL']}/refer/{lead.id}"
    html_body = (
        f"<p>Hi {first_name},</p>"
        f"<p>If {brand} was useful, we'd be grateful for a referral. Share "
        f"your link with a colleague:</p>"
        f"<p><a href=\"{referral_url}\">{referral_url}</a></p>"
    )
    return gmail_client.send_email(
        to_email=lead.email, subject=f"Know someone who'd benefit from {brand}?",
        html_body=html_body, unsubscribe_url=_unsubscribe_url(lead.id),
    )
