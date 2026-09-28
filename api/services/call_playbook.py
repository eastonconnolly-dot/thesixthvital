"""call_playbook.py — Phase 6, "Sales handoff readiness": once 10+
transcribed sales calls exist, generate a structured call playbook
(objections + responses, close lines) from those transcripts via one
Claude call, JSON-schema-constrained per the same output_config convention
already used by api/services/patient_sim.py and content/ingest.py.

    generate_playbook()  -- the whole flow; persists a CallPlaybook row.

Persistence lives in models.CallPlaybook, one row per generation; admin can
regenerate at will via POST /admin/playbook/regenerate (api/routes/playbook.py).
"""

import json

from flask import current_app

from extensions import db
from models import CallPlaybook
from services import claude_client

MIN_TRANSCRIPTS_FOR_PLAYBOOK = 10
PLAYBOOK_MAX_TOKENS = 3000

PLAYBOOK_SCHEMA = {
    "type": "object",
    "properties": {
        "objections": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "items": {
                "type": "object",
                "properties": {
                    "objection": {"type": "string"},
                    "response": {"type": "string"},
                },
                "required": ["objection", "response"],
                "additionalProperties": False,
            },
        },
        "close_lines": {
            "type": "array",
            "minItems": 1,
            "maxItems": 15,
            "items": {"type": "string"},
        },
    },
    "required": ["objections", "close_lines"],
    "additionalProperties": False,
}


def _transcript_text(raw):
    """A transcript field might land as a plain string or as structured
    JSON (e.g. a list of {"speaker":..., "text":...} turns, the way
    PracticeSession.transcript is shaped) -- normalize either into text
    for the prompt rather than assuming one shape."""
    if isinstance(raw, str):
        return raw
    try:
        return json.dumps(raw, indent=2)
    except (TypeError, ValueError):
        return str(raw)


def _plan_playbook(transcripts):
    """One Claude call, JSON-schema-constrained, turning N sales-call
    transcripts into a playbook. Mirrors services/patient_sim.py's
    score_encounter() / content/ingest.py's _plan_content() pattern."""
    joined = "\n\n---CALL TRANSCRIPT---\n\n".join(_transcript_text(t) for t in transcripts)
    prompt = (
        "You are a sales enablement strategist for Sixth Vital, a physician-communication "
        "training company selling $10k+ intensives and cohort programs. Below are "
        f"{len(transcripts)} transcripts of real sales/discovery calls. Read through "
        "all of them and produce a call playbook a delegated closer (not the founder) "
        "could use to run these calls independently:\n\n"
        "- objections: every distinct objection prospects actually raised across these "
        "calls (price, timing, 'need to check with my partner', skepticism about ROI, "
        "etc.), each paired with the most effective response actually used or clearly "
        "implied by how the call proceeded.\n"
        "- close_lines: concrete lines/phrases that moved a call toward a close "
        "(booking a deposit, agreeing to a proposal, etc.), drawn from what actually "
        "worked in these transcripts.\n\n"
        f"TRANSCRIPTS:\n{joined}"
    )
    resp = claude_client.client().messages.create(
        model=claude_client.model(),
        max_tokens=PLAYBOOK_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": PLAYBOOK_SCHEMA}},
    )
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)


def _sales_call_transcripts():
    """Integration point for "a source of transcribed sales calls". This
    depends on a call-transcript model another engineer is building in
    parallel (the pre-call-qualifier / call-to-proposal work) -- as of this
    writing, api/models.py has Deal fields referencing that pipeline
    (`qualifier_brief`, `proposal_pending_review`, etc. -- see
    services/qualifier_chat.py and services/call_to_proposal.py in Deal's
    own comments) but no persisted call-transcript model yet.

    Expected shape once it lands (documented, not guessed at import time):
        from models import SalesCall
        # SalesCall rows expected to expose:
        #   .transcript      -- Text or JSON: the call transcript
        #   a link to the deal/lead it belongs to, e.g. .deal_id

    Queries defensively: imports SalesCall inside a try/except ImportError
    so this no-ops cleanly (clear log message) if that model hasn't landed,
    and self-heals the moment it has, with no code change required here.

    Returns (available: bool, transcripts: list[str|dict]).
    """
    try:
        from models import SalesCall
    except ImportError:
        current_app.logger.info(
            "call_playbook._sales_call_transcripts: models.SalesCall is not "
            "defined yet (call-to-proposal transcript storage hasn't landed) "
            "-- no-op."
        )
        return False, []

    rows = SalesCall.query.all()
    transcripts = [getattr(r, "transcript", None) for r in rows]
    transcripts = [t for t in transcripts if t]  # a row can exist without a transcript yet
    return True, transcripts


def transcript_status():
    """Public wrapper around _sales_call_transcripts() for callers (e.g.
    the admin view) that just want to know how much data is available
    without generating anything. Returns (available: bool, count: int)."""
    available, transcripts = _sales_call_transcripts()
    return available, len(transcripts)


def generate_playbook():
    """Generates and persists a new CallPlaybook, if there's enough data.

    Returns a dict:
        {"available": False, "reason": str, "playbook": None}
            No call-transcript source exists yet at all.
        {"available": True, "playbook": None, "transcript_count": N,
         "reason": "not enough data yet (N/10)"}
            The source exists but fewer than MIN_TRANSCRIPTS_FOR_PLAYBOOK
            transcripts are available -- deliberately does NOT call Claude
            on a thin sample.
        {"available": True, "playbook": CallPlaybook, "transcript_count": N}
            A new CallPlaybook row was generated and committed.
    """
    available, transcripts = _sales_call_transcripts()
    if not available:
        return {"available": False, "reason": "no call-transcript source configured yet", "playbook": None}

    count = len(transcripts)
    if count < MIN_TRANSCRIPTS_FOR_PLAYBOOK:
        return {
            "available": True,
            "playbook": None,
            "transcript_count": count,
            "reason": f"not enough data yet ({count}/{MIN_TRANSCRIPTS_FOR_PLAYBOOK})",
        }

    plan = _plan_playbook(transcripts)
    row = CallPlaybook(
        transcript_count=count,
        objections=plan["objections"],
        close_lines=plan["close_lines"],
    )
    db.session.add(row)
    db.session.commit()
    return {"available": True, "playbook": row, "transcript_count": count}


def latest_playbook():
    return CallPlaybook.query.order_by(CallPlaybook.generated_at.desc()).first()
