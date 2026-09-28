"""Positive-reply classifier: given an inbound reply's text, asks Claude
whether it reads as positive-intent (wants to talk, wants a call, asks about
pricing/next steps, etc. -- as opposed to "not interested", an out-of-office,
or a bounce-adjacent auto-reply), and if so drafts a short reply plus
proposes real call slots via `api/services/calendar_client.py`'s
`available_slots()`. Writes a `MessageDraft` row that
`api/routes/inbox.py` reads for the one-click approve/book/dismiss UI.

No stub mode for Claude, same as `api/services/patient_sim.py` -- there's no
meaningful fake classification. With `ANTHROPIC_API_KEY` unset,
`classify_reply()` logs and returns `None` (a clean skip, not a crash); the
message still shows up in the inbox, just without a drafted reply.
"""

import json
import logging
import os
import sys

_API_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "api"))
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

from flask import current_app

from extensions import db
from models import Message, MessageDraft
from services import calendar_client, claude_client

log = logging.getLogger(__name__)

MAX_TOKENS = 600
SLOTS_TO_PROPOSE = 3

SYSTEM_PROMPT = (
    "You are triaging inbound email replies for Sixth Vital, a physician-communication "
    "training company running cold outreach sequences. Given a lead's reply to one "
    "of our nurture emails, decide whether it reads as positive-intent -- they want "
    "to talk, ask about pricing or scheduling, express real interest, or ask a "
    "substantive question -- as opposed to a flat no, an out-of-office autoresponder, "
    "an unsubscribe request, or a one-line brush-off. If positive, draft a short, "
    "warm, specific reply (2-4 sentences, referencing something they actually said) "
    "that offers to set up a brief call, in the voice of a helpful colleague, not a "
    "salesperson. If not positive, leave draft_reply null."
)

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "positive": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "string"},
        "draft_reply": {"type": ["string", "null"]},
    },
    "required": ["positive", "confidence", "reasoning", "draft_reply"],
    "additionalProperties": False,
}


def classify_reply(message_id):
    """Classifies the inbound `Message` with id `message_id`, writes (or
    updates) its `MessageDraft`, and returns that draft. Returns `None` if
    `ANTHROPIC_API_KEY` isn't configured or the message doesn't exist."""
    if not current_app.config.get("ANTHROPIC_API_KEY"):
        log.info("classifier: ANTHROPIC_API_KEY not configured, skipping message %s", message_id)
        return None

    message = db.session.get(Message, message_id)
    if not message:
        return None

    result = _classify_with_claude(message.body or "", message.lead)

    proposed_slots = []
    if result["positive"]:
        try:
            slots = calendar_client.available_slots(days_ahead=10)
            proposed_slots = [s.isoformat() for s in slots[:SLOTS_TO_PROPOSE]]
        except Exception as exc:
            log.warning("classifier: could not fetch call slots for message %s: %s", message_id, exc)

    draft = message.draft or MessageDraft(message_id=message.id)
    draft.positive = result["positive"]
    draft.confidence = result["confidence"]
    draft.draft_body = result.get("draft_reply") or ""
    draft.proposed_slots = proposed_slots
    db.session.add(draft)
    db.session.commit()
    return draft


def _classify_with_claude(reply_text, lead):
    lead_desc = f"{lead.name} ({lead.track}, {lead.org or 'org unknown'})" if lead else "unknown lead"
    user_content = f"Lead: {lead_desc}\n\nTheir reply:\n\"\"\"\n{reply_text}\n\"\"\""

    resp = claude_client.client().messages.create(
        model=claude_client.model(),
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
        output_config={"format": {"type": "json_schema", "schema": CLASSIFY_SCHEMA}},
    )
    text = next(b.text for b in resp.content if b.type == "text").strip()

    try:
        data = json.loads(text)
    except ValueError:
        log.warning("classifier: could not parse Claude response as JSON: %r", text)
        return {"positive": False, "confidence": 0.0, "reasoning": "parse_error", "draft_reply": None}

    return {
        "positive": bool(data.get("positive")),
        "confidence": float(data.get("confidence") or 0.0),
        "reasoning": data.get("reasoning", ""),
        "draft_reply": data.get("draft_reply"),
    }
