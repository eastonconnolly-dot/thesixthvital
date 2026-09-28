"""Checks Gmail thread history for a reply on an outbound `Message`'s
`thread_id`. On a detected reply:

  1. Marks the outbound `Message` `replied=True`.
  2. Records the reply itself as a new inbound `Message` row (so there's
     real reply text for `engine.classifier.classify_reply()` to work with
     -- see `_reply_text` below for how that text is pulled out of Gmail's
     response).
  3. Stops every active `SequenceEnrollment` for that lead
     (`status="stopped"`, `stop_reason="replied"`) so the nurture sequence
     doesn't keep emailing someone who already wrote back.
  4. Best-effort kicks off classification for the new inbound message, so
     a drafted reply is usually already waiting by the time an admin opens
     the inbox (`api/routes/inbox.py`). A classifier failure here (including
     `ANTHROPIC_API_KEY` being unset) is caught and logged -- it never stops
     reply detection itself from doing its job.

Call `check_for_replies()` on a schedule, same as `engine.sequences.tick()`
-- Gmail's API has no push-on-reply hook wired here, so this is poll-based.
"""

import logging
import os
import sys

_API_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "api"))
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

from extensions import db
from models import Message, SequenceEnrollment
from services import gmail_client

log = logging.getLogger(__name__)


def check_for_replies(messages=None, classify=True):
    """`messages`: optional iterable of outbound `Message` rows to check
    (defaults to every outbound, not-yet-`replied`, thread-bearing message).
    `classify`: set False to skip the best-effort classification hand-off
    (useful in tests that only care about reply detection itself).

    Returns the list of newly-created inbound `Message.id`s.

    A Gmail API failure for one thread is logged and skipped -- it doesn't
    stop the rest of the batch, and the message stays eligible to be
    re-checked on the next call.
    """
    if messages is None:
        messages = Message.query.filter(
            Message.direction == "outbound", Message.replied.is_(False), Message.thread_id.isnot(None),
        ).all()

    new_inbound_ids = []
    for message in messages:
        try:
            thread_messages = gmail_client.list_thread_replies(message.thread_id)
        except Exception as exc:
            log.warning("reply_detection: could not fetch thread %s: %s", message.thread_id, exc)
            continue

        if len(thread_messages or []) <= 1:
            continue  # no reply yet -- just our own original send

        message.replied = True
        _stop_enrollments_for_lead(message.lead_id)

        inbound = Message(
            lead_id=message.lead_id, direction="inbound", channel="email",
            thread_id=message.thread_id,
            subject=f"Re: {message.subject}" if message.subject else None,
            body=_reply_text(thread_messages),
        )
        db.session.add(inbound)
        db.session.flush()  # assign inbound.id before we hand it to the classifier
        new_inbound_ids.append(inbound.id)

        if classify:
            _try_classify(inbound.id)

    if new_inbound_ids:
        db.session.commit()
    return new_inbound_ids


def _reply_text(thread_messages):
    """Pulls a plain-text-ish body out of the latest message in the thread.
    Gmail's API always returns a `snippet` (a short plain-text preview)
    regardless of the underlying MIME structure, which is a far more
    reliable target than trying to walk arbitrary multipart/base64 payload
    trees for a classifier that only needs the gist of the reply anyway."""
    latest = thread_messages[-1]
    return latest.get("snippet") or ""


def _stop_enrollments_for_lead(lead_id):
    active = SequenceEnrollment.query.filter_by(lead_id=lead_id, status="active").all()
    for enrollment in active:
        enrollment.status = "stopped"
        enrollment.stop_reason = "replied"


def _try_classify(inbound_message_id):
    try:
        from . import classifier
        classifier.classify_reply(inbound_message_id)
    except Exception as exc:
        log.warning("reply_detection: classification failed for message %s: %s", inbound_message_id, exc)
