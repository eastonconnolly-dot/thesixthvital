"""Admin inbox for classifier-drafted replies -- Phase 2's `outreach/inbox/`
surfaced as a standalone blueprint (not a separate Flask app) so it plugs
into the existing admin session/auth, per the build brief. See
`outreach/INTEGRATION.md` for the one-line registration this needs in
`api/app.py` (not edited here -- another engineer is working in that file).
"""

from datetime import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from extensions import db
from models import Lead, Message, MessageDraft, SequenceEnrollment
from routes.admin import admin_required
from services import calendar_client, gmail_client

bp = Blueprint("inbox", __name__, url_prefix="/admin/inbox")


def _stopped_for_reply_lead_ids():
    return db.session.query(SequenceEnrollment.lead_id).filter(SequenceEnrollment.stop_reason == "replied")


def _reply_messages_query():
    """Inbound Messages that are either flagged as a reply directly, or
    belong to a lead whose enrollment was stopped for exactly that reason
    (see outreach/engine/reply_detection.py) -- excludes anything whose
    draft has already been dismissed as not-a-fit."""
    return (
        Message.query
        .outerjoin(MessageDraft, MessageDraft.message_id == Message.id)
        .filter(Message.direction == "inbound")
        .filter(db.or_(
            Message.replied.is_(True),
            Message.lead_id.in_(_stopped_for_reply_lead_ids()),
        ))
        .filter(db.or_(MessageDraft.id.is_(None), MessageDraft.dismissed.is_(False)))
        .order_by(db.desc(MessageDraft.confidence), db.desc(Message.sent_at))
    )


@bp.get("")
@admin_required
def list_replies():
    messages = _reply_messages_query().all()
    return render_template("admin/inbox.html", messages=messages)


@bp.post("/<int:message_id>/send")
@admin_required
def send_reply(message_id):
    message = Message.query.get_or_404(message_id)
    draft = message.draft
    if not draft or not draft.draft_body:
        flash("No drafted reply to send for this message.", "error")
        return redirect(url_for("inbox.list_replies"))

    lead = message.lead
    unsubscribe_url = f"{current_app.config['API_BASE_URL']}/unsubscribe?lead_id={lead.id}"
    sent = gmail_client.send_email(
        to_email=lead.email,
        subject=f"Re: {message.subject}" if message.subject else "Re:",
        html_body=draft.draft_body,
        unsubscribe_url=unsubscribe_url,
        thread_id=message.thread_id,
    )
    db.session.add(Message(
        lead_id=lead.id, direction="outbound", channel="email",
        thread_id=sent.get("thread_id"), subject=message.subject, body=draft.draft_body,
    ))
    draft.approved = True
    db.session.commit()
    flash("Reply sent.", "success")
    return redirect(url_for("inbox.list_replies"))


@bp.post("/<int:message_id>/book")
@admin_required
def book_call(message_id):
    message = Message.query.get_or_404(message_id)
    draft = message.draft
    if not draft or not draft.proposed_slots:
        flash("No proposed call slots for this message.", "error")
        return redirect(url_for("inbox.list_replies"))

    lead = message.lead
    slot_start = datetime.fromisoformat(draft.proposed_slots[0])
    try:
        calendar_client.book_slot(slot_start, summary=f"RPSAS call — {lead.name}", attendee_email=lead.email)
    except Exception as exc:
        current_app.logger.warning("inbox: failed to book call for lead %s: %s", lead.id, exc)
        flash("Could not book the call — check Google Calendar credentials.", "error")
        return redirect(url_for("inbox.list_replies"))

    lead.status = "booked"
    draft.approved = True
    db.session.commit()
    flash(f"Call booked for {slot_start.strftime('%b %d, %Y %I:%M %p')}.", "success")
    return redirect(url_for("inbox.list_replies"))


@bp.post("/<int:message_id>/dismiss")
@admin_required
def dismiss(message_id):
    message = Message.query.get_or_404(message_id)
    draft = message.draft or MessageDraft(message_id=message.id, positive=False, confidence=0.0)
    draft.dismissed = True
    db.session.add(draft)
    db.session.commit()
    flash("Dismissed.", "success")
    return redirect(url_for("inbox.list_replies"))
