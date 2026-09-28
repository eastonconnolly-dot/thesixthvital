"""Onboarding automation (Phase 6): everything that should happen the
moment a deal's proposal gets signed, with no founder action required.

`trigger_onboarding(deal)` is the entry point. It must be called once a
`SignatureRequest` reaches status="signed" -- see api/INTEGRATION.md for the
exact one-line addition needed in routes/esign.py (out of scope for this
change; that file is another engineer's area).

Idempotent: guarded by `Deal.onboarding_triggered_at`, so calling it twice
(e.g. a retried webhook, a flaky esign POST) never double-sends the welcome
email, double-books calendar holds, or double-orders badges.

Also home to `send_delivery_reminders()`, the 7/3/1-day-before-delivery
reminder chain (`flask send-delivery-reminders`, see api/app.py). This does
NOT reuse outreach/engine/sequences.py's SequenceEnrollment machinery --
see the docstring on send_delivery_reminders for why.
"""

import secrets
from datetime import datetime, time, timedelta, timezone

from flask import current_app

from extensions import db
from models import CohortRoster, Deal, IntakeForm, UploadLink, utcnow
from services import calendar_client, gmail_client
from services.print_vendor import submit_badge_print_order

log_prefix = "onboarding"


def _token():
    return secrets.token_urlsafe(24)


def _unsubscribe_url(lead):
    return f"{current_app.config['API_BASE_URL']}/unsubscribe?lead_id={lead.id}"


def _initial_participants(deal):
    """Who gets a baseline-video upload link at signing time. A cohort's
    real roster doesn't exist yet (it comes from the sponsor roster form,
    submitted later) -- so before delivery, the only participant we know
    about for certain is the deal's own primary contact (the lead)."""
    lead = deal.lead
    return [{"name": lead.name, "email": lead.email}]


def _send_welcome_email(deal, lead, intake_url, upload_urls):
    brand = current_app.config["BRAND_NAME"]
    upload_lines = "".join(
        f'<li><a href="{u["url"]}">{u["name"]}: upload your baseline video</a></li>'
        for u in upload_urls
    )
    html_body = f"""
    <p>Hi {lead.name.split(" ")[0] if lead.name else "there"},</p>
    <p>Welcome to {brand} -- your session is officially on the books. Here's
    everything to take care of before delivery day:</p>
    <ol>
      <li><a href="{intake_url}">Complete your intake form</a> (emergency
      contact, sizing, accessibility, and any special requests).</li>
      {upload_lines}
    </ol>
    <p>We'll follow up with delivery-day logistics as the date gets closer.
    Reach out any time with questions.</p>
    """
    unsubscribe_url = _unsubscribe_url(lead)
    sent = gmail_client.send_email(
        to_email=lead.email,
        subject=f"Welcome to {brand} -- next steps before your session",
        html_body=html_body,
        unsubscribe_url=unsubscribe_url,
    )
    return sent


def _delivery_datetime(deal):
    """Delivery holds default to 9am local-to-the-calendar (calendar_client
    doesn't carry timezone config beyond the configured GOOGLE_CALENDAR_ID,
    so this matches the 9am WORKDAY_START_HOUR convention already used by
    calendar_client.available_slots)."""
    return datetime.combine(deal.delivery_date, time(9, 0), tzinfo=timezone.utc)


def _book_calendar_holds(deal, lead):
    """Best-effort: a Calendar API hiccup shouldn't block the rest of
    onboarding (mirrors services/hub_sync.py's "never let an external call
    block the caller" pattern). Returns the list of holds actually booked."""
    booked = []

    delivery_dt = _delivery_datetime(deal)
    try:
        calendar_client.book_slot(
            delivery_dt, summary=f"{current_app.config['BRAND_NAME']} delivery -- {lead.name}",
            attendee_email=lead.email,
        )
        booked.append("delivery")
    except Exception as exc:
        current_app.logger.warning("%s: delivery calendar hold failed for deal=%s: %s", log_prefix, deal.id, exc)

    checkin_dt = delivery_dt + timedelta(days=30)
    try:
        calendar_client.book_slot(
            checkin_dt, summary=f"{current_app.config['BRAND_NAME']} 30-day check-in -- {lead.name}",
            attendee_email=lead.email,
        )
        booked.append("checkin_30day")
    except Exception as exc:
        current_app.logger.warning("%s: check-in calendar hold failed for deal=%s: %s", log_prefix, deal.id, exc)

    return booked


def ensure_calendar_holds(deal):
    """Books the delivery + 30-day-check-in calendar holds for `deal` if
    they haven't been booked yet and `deal.delivery_date` is now known.

    Split out from trigger_onboarding() (and idempotent on its own
    `calendar_holds_booked_at`, not `onboarding_triggered_at`) because
    onboarding is one-shot: a deal can reach trigger_onboarding() before its
    delivery_date is known (a call transcript that didn't state one, or a
    self-serve package checkout with no discovery call at all). Without this
    split, calendar holds would be skipped at trigger_onboarding() time and
    then skipped forever, even after delivery_date is filled in later --
    trigger_onboarding() never runs a second time to retry them. Call this
    again any time delivery_date is set or corrected on an already-onboarded
    deal (see routes/admin.py's set_delivery_date)."""
    if deal.calendar_holds_booked_at is not None:
        return []
    if not deal.delivery_date:
        return []

    booked = _book_calendar_holds(deal, deal.lead)
    deal.calendar_holds_booked_at = utcnow()
    db.session.commit()
    return booked


def _submit_badge_order(deal, participant_names):
    try:
        submit_badge_print_order(deal, participant_names)
        return True
    except Exception as exc:
        current_app.logger.warning("%s: badge print order failed for deal=%s: %s", log_prefix, deal.id, exc)
        return False


def trigger_onboarding(deal):
    """Idempotent. Returns a summary dict of what ran (or
    {"status": "already_triggered"} on a repeat call)."""
    if deal.onboarding_triggered_at is not None:
        return {"status": "already_triggered"}

    lead = deal.lead
    result = {
        "status": "ok", "welcome_email_sent": False, "intake_form_created": False,
        "upload_links_created": 0, "calendar_holds": [], "badge_order_submitted": False,
        "cohort_roster_created": False,
    }

    intake = IntakeForm(deal_id=deal.id, token=_token())
    db.session.add(intake)

    participants = _initial_participants(deal)
    upload_urls = []
    for p in participants:
        link = UploadLink(
            deal_id=deal.id, participant_name=p["name"], participant_email=p["email"], token=_token(),
        )
        db.session.add(link)
        upload_urls.append({"name": p["name"], "url": f"{current_app.config['API_BASE_URL']}/upload/{link.token}"})
    result["upload_links_created"] = len(upload_urls)

    roster = None
    if lead.track == "program":
        roster = CohortRoster(deal_id=deal.id, token=_token())
        db.session.add(roster)
        result["cohort_roster_created"] = True

    db.session.flush()  # so intake.token etc. are all assigned before we build the email

    intake_url = f"{current_app.config['API_BASE_URL']}/intake/{intake.token}"
    _send_welcome_email(deal, lead, intake_url, upload_urls)
    result["welcome_email_sent"] = True
    result["intake_form_created"] = True

    result["badge_order_submitted"] = _submit_badge_order(deal, [p["name"] for p in participants])

    if roster is not None:
        roster_url = f"{current_app.config['API_BASE_URL']}/roster/{roster.token}"
        html_body = f"""
        <p>Hi {lead.name.split(" ")[0] if lead.name else "there"},</p>
        <p>As the program sponsor, please share your participant roster and
        confirm room/AV requirements so we can prep for delivery day:</p>
        <p><a href="{roster_url}">Submit your roster &amp; room/AV checklist</a></p>
        """
        gmail_client.send_email(
            to_email=lead.email,
            subject=f"{current_app.config['BRAND_NAME']} -- participant roster & room/AV checklist",
            html_body=html_body,
            unsubscribe_url=_unsubscribe_url(lead),
        )

    deal.onboarding_triggered_at = utcnow()
    db.session.commit()

    result["calendar_holds"] = ensure_calendar_holds(deal)
    return result


# ── 7/3/1-day-before-delivery reminder chain ─────────────────────────────
#
# Deliberately NOT built on outreach/engine/sequences.py's Sequence /
# SequenceStep / SequenceEnrollment machinery, even though it exists and
# `tick()` is generic-looking at first glance:
#   - SequenceEnrollment.delay_days counts forward from enrollment / the
#     previous step, not backward from a fixed target date (delivery_date);
#     modeling "remind me at T-7, T-3, T-1" would need three separate
#     enrollments seeded with hand-computed offsets and re-computed every
#     time delivery_date changes, which is more fragile than a direct query.
#   - tick() is throttled by SEQUENCE_DAILY_SEND_CAP and skips suppressed
#     leads -- appropriate for cold nurture outreach, wrong for a
#     transactional reminder to someone who already signed and paid.
#   - Enrollment is keyed to Lead, but the reminder is really about the
#     Deal (a lead can have >1 deal with different delivery dates).
# A dedicated query + three boolean flags on Deal is simpler and correct.

def send_delivery_reminders(now=None):
    """Sends a reminder email to any deal whose delivery_date is exactly
    7, 3, or 1 day(s) out and whose corresponding reminder_*_sent flag isn't
    already set. Safe to call more than once a day (each day only ever
    matches at most one of the three thresholds for a given deal, and the
    flag prevents a resend even if delivery_date happens to net out to the
    same offset twice, e.g. after being edited)."""
    now = now or datetime.now(timezone.utc)
    today = now.date()
    summary = {"sent": 0, "skipped": 0, "errors": 0}

    thresholds = ((7, "reminder_7d_sent"), (3, "reminder_3d_sent"), (1, "reminder_1d_sent"))

    deals = (
        Deal.query
        .filter(Deal.delivery_date.isnot(None))
        .filter(Deal.stage.notin_(["closed_lost"]))
        .all()
    )
    for deal in deals:
        days_out = (deal.delivery_date - today).days
        for threshold, flag in thresholds:
            if days_out != threshold:
                continue
            if getattr(deal, flag):
                summary["skipped"] += 1
                continue
            lead = deal.lead
            try:
                _send_reminder_email(deal, lead, threshold)
                setattr(deal, flag, True)
                db.session.commit()
                summary["sent"] += 1
            except Exception as exc:
                current_app.logger.warning(
                    "%s: reminder send failed for deal=%s threshold=%s: %s", log_prefix, deal.id, threshold, exc
                )
                summary["errors"] += 1

    return summary


def _send_reminder_email(deal, lead, days_out):
    brand = current_app.config["BRAND_NAME"]
    plural = "day" if days_out == 1 else "days"
    html_body = f"""
    <p>Hi {lead.name.split(" ")[0] if lead.name else "there"},</p>
    <p>Your {brand} session is {days_out} {plural} away, on
    {deal.delivery_date.strftime("%B %d, %Y")}.</p>
    <p>If you haven't already, please complete your intake form and upload
    your baseline video -- the links are in your welcome email. Reach out
    any time with questions before delivery day.</p>
    """
    return gmail_client.send_email(
        to_email=lead.email,
        subject=f"{days_out} {plural} until your {brand} session",
        html_body=html_body,
        unsubscribe_url=_unsubscribe_url(lead),
    )
