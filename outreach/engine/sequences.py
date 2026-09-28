"""The `tick()` cron advancer for `SequenceEnrollment` rows.

Call `tick()` on a schedule (cron / Render cron job / APScheduler -- the
scheduler wiring itself is out of scope here; this module only implements
the advancer). Each call:

  1. Finds active enrollments whose `next_fire_at` has passed.
  2. Renders the current step's template against the lead and sends it
     (email via `api/services/gmail_client.py`; SMS -- gated, see below).
  3. Logs the send as a `Message` row, advances `current_step_index`, and
     computes the next `next_fire_at`.
  4. Marks the enrollment `"completed"` once it runs out of steps.

`SequenceStep.delay_days` is the gap since the *previous* step fired (not a
cumulative day-count since enrollment) -- step 0 is typically `delay_days=0`
(fires immediately on enrollment), and each later step's `delay_days` is
added to `now` (the time the previous step actually sent) to get the next
`next_fire_at`. See `outreach/seed_templates.py` for the real four-touch
copy this drives.

Throttling: every send checks the mailbox's daily cap
(`SEQUENCE_DAILY_SEND_CAP`, default 80/day) computed from today's outbound
`Message` count, ramped up linearly from `SEQUENCE_RAMP_START_CAP` over
`SEQUENCE_RAMP_DAYS` (default 14) starting from the first outbound message
ever sent (used as a proxy for "mailbox warm-up start date" -- there's no
separate field tracking that). Enrollments that are due but can't send
because the cap is hit are simply left due for the next `tick()` call
(`next_fire_at` is untouched), not dropped.

SMS is fully gated behind `QUO_API_KEY`: unset means every SMS step is
skipped (not sent, not an error) and the enrollment advances past it. Even
with a key configured, SMS only fires for `physician`-track leads with a
phone number and prior email engagement (an outbound email `Message` already
logged for that lead) -- anyone else's SMS step is skipped the same way.
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

_API_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "api"))
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

import requests
from flask import current_app

from extensions import db
from models import Message, SequenceEnrollment, SuppressedEmail
from services import gmail_client

log = logging.getLogger(__name__)


def _now(now=None):
    return now or datetime.now(timezone.utc)


def _aware(dt):
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _today_bounds(now):
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _sends_today(now):
    start, end = _today_bounds(now)
    return Message.query.filter(
        Message.direction == "outbound", Message.sent_at >= start, Message.sent_at < end,
    ).count()


def _daily_cap(now):
    cfg = current_app.config
    cap_final = cfg["SEQUENCE_DAILY_SEND_CAP"]
    cap_start = cfg["SEQUENCE_RAMP_START_CAP"]
    ramp_days = max(cfg["SEQUENCE_RAMP_DAYS"], 1)

    first = Message.query.filter(Message.direction == "outbound").order_by(Message.sent_at.asc()).first()
    if not first or not first.sent_at:
        return cap_start  # no sending history yet -- start of the ramp

    days_elapsed = (now - _aware(first.sent_at)).days
    if days_elapsed >= ramp_days:
        return cap_final
    if days_elapsed <= 0:
        return cap_start
    fraction = days_elapsed / ramp_days
    return int(round(cap_start + fraction * (cap_final - cap_start)))


def _render(template, lead):
    if not template:
        return template
    first_name = (lead.name or "").split(" ")[0] or lead.name or ""
    ctx = {"name": lead.name or "", "first_name": first_name, "org": lead.org or "your organization"}
    out = template
    for key, value in ctx.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def _has_prior_email_engagement(lead_id):
    return Message.query.filter_by(lead_id=lead_id, direction="outbound", channel="email").first() is not None


def _sms_eligible(lead):
    return bool(lead.track == "physician" and lead.phone and _has_prior_email_engagement(lead.id))


def _advance(enrollment, steps, now):
    enrollment.current_step_index += 1
    if enrollment.current_step_index >= len(steps):
        enrollment.status = "completed"
        enrollment.next_fire_at = None
    else:
        next_step = steps[enrollment.current_step_index]
        enrollment.next_fire_at = now + timedelta(days=next_step.delay_days)


def enroll_lead(lead, sequence, start_at=None):
    """Creates an active enrollment for `lead` in `sequence`, due at
    `start_at` (default: now + the first step's delay_days, i.e. immediately
    for a typical delay_days=0 first step)."""
    now = _now(start_at)
    first_step = sequence.steps[0] if sequence.steps else None
    enrollment = SequenceEnrollment(
        sequence_id=sequence.id,
        lead_id=lead.id,
        current_step_index=0,
        next_fire_at=now + timedelta(days=first_step.delay_days) if first_step else None,
        status="active" if first_step else "completed",
    )
    db.session.add(enrollment)
    db.session.commit()
    return enrollment


def tick(now=None):
    """Advances every due active enrollment by one step. Returns a summary
    dict. Safe to call repeatedly -- each send commits immediately, so a
    crash partway through only loses progress on the send in flight, not
    ones already recorded."""
    now = _now(now)
    summary = {
        "sent_email": 0, "sent_sms": 0, "sms_skipped": 0,
        "skipped_capped": 0, "skipped_suppressed": 0, "completed": 0, "errors": 0,
    }

    cap = _daily_cap(now)
    sent_today = _sends_today(now)

    due = (
        SequenceEnrollment.query
        .filter(SequenceEnrollment.status == "active")
        .filter(SequenceEnrollment.next_fire_at.isnot(None))
        .filter(SequenceEnrollment.next_fire_at <= now)
        .order_by(SequenceEnrollment.next_fire_at.asc())
        .all()
    )

    for enrollment in due:
        lead = enrollment.lead
        sequence = enrollment.sequence
        steps = sequence.steps

        if enrollment.current_step_index >= len(steps):
            enrollment.status = "completed"
            enrollment.next_fire_at = None
            db.session.commit()
            summary["completed"] += 1
            continue

        step = steps[enrollment.current_step_index]

        if step.channel == "sms":
            if not current_app.config.get("QUO_API_KEY") or not _sms_eligible(lead):
                summary["sms_skipped"] += 1
                _advance(enrollment, steps, now)
                db.session.commit()
                continue
            if sent_today >= cap:
                summary["skipped_capped"] += 1
                continue
            try:
                _send_sms_step(lead, step, enrollment)
                sent_today += 1
                summary["sent_sms"] += 1
            except Exception as exc:
                log.error("tick: sms send failed for lead=%s step=%s: %s", lead.id, step.id, exc)
                summary["errors"] += 1
                continue
            _advance(enrollment, steps, now)
            db.session.commit()
            continue

        # channel == "email"
        if is_suppressed(lead.email):
            enrollment.status = "stopped"
            enrollment.stop_reason = "suppressed"
            db.session.commit()
            summary["skipped_suppressed"] += 1
            continue

        if sent_today >= cap:
            summary["skipped_capped"] += 1
            continue

        try:
            _send_email_step(lead, step, enrollment)
            sent_today += 1
            summary["sent_email"] += 1
        except Exception as exc:
            log.error("tick: email send failed for lead=%s step=%s: %s", lead.id, step.id, exc)
            summary["errors"] += 1
            continue

        _advance(enrollment, steps, now)
        db.session.commit()

    return summary


def is_suppressed(email):
    if not email:
        return False
    return SuppressedEmail.query.filter_by(email=email.strip().lower()).first() is not None


def _send_email_step(lead, step, enrollment):
    subject = _render(step.subject, lead)
    body_html = _render(step.body, lead)
    unsubscribe_url = f"{current_app.config['API_BASE_URL']}/unsubscribe?lead_id={lead.id}"

    prior = Message.query.filter_by(lead_id=lead.id, channel="email").order_by(Message.sent_at.desc()).first()
    thread_id = prior.thread_id if prior else None

    sent = gmail_client.send_email(
        to_email=lead.email, subject=subject, html_body=body_html,
        unsubscribe_url=unsubscribe_url, thread_id=thread_id,
    )
    db.session.add(Message(
        lead_id=lead.id, direction="outbound", channel="email",
        thread_id=sent.get("thread_id"), subject=subject, body=body_html,
    ))


def _send_sms_step(lead, step, enrollment):
    """SMS send via Quo (QUO_API_KEY). Only reached once `tick()` has
    already confirmed the key is configured and the lead is eligible."""
    text = _render(step.body, lead)
    resp = requests.post(
        "https://api.quo.com/v1/messages",
        headers={"Authorization": f"Bearer {current_app.config['QUO_API_KEY']}"},
        json={"to": lead.phone, "body": text},
        timeout=15,
    )
    resp.raise_for_status()
    db.session.add(Message(lead_id=lead.id, direction="outbound", channel="sms", body=text))
