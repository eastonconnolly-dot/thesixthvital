"""Claude-driven pre-call qualifier (Phase 6). Runs a short structured chat
with a lead after a qualified application, before they're allowed to book a
call with the founder — situation, timeline, budget, decision-maker,
objections. Shaped like services/patient_sim.py's conversation loop: the
lead is "user", the qualifier is "assistant". `complete_qualifier` follows
the same JSON-schema-constrained-output conventions documented in
patient_sim.SCORE_SCHEMA (no min/max on integers — n/a here since this
schema has none; empty-string sentinel instead of a ["string","null"]
union+null-enum for the one nullable field, `recommended_package`)."""

import json

from services import claude_client
from services.notify_utils import first_name as _first_name

MAX_TOKENS_REPLY = 200
MAX_TOKENS_COMPLETE = 700

# The two self-serve PACKAGES keys the brief names explicitly for a
# sub-$10k lead ("Practice trial, applicant cohort seat, RPSAS Taste").
# "Practice trial" isn't a PACKAGES-keyed deposit product — it's the
# existing Practice subscription flow (services/stripe_client
# .create_subscription_checkout_session, api/routes/practice.py) — so it's
# offered by the frontend as a parallel link to /practice/login rather than
# a `recommended_package` value here. See qualifier/INTEGRATION.md.
SELF_SERVE_PACKAGES = ("rpsas_taste", "applicant_cohort_seat")

QUALIFIER_SYSTEM = (
    "You are RPSAS's pre-call qualifier, a warm, efficient assistant that runs a short "
    "structured conversation with a lead before they're allowed to book a call with the "
    "founder. Over the course of the conversation you must get a clear read on five things, "
    "in whatever order feels natural: (1) situation - what's driving their interest right "
    "now, (2) timeline - how soon they need this, (3) budget - whether they can invest at "
    "the founder-led coaching price point (four figures and up), (4) decision-maker - "
    "whether they're the one who can say yes or need someone else's sign-off, (5) "
    "objections - any hesitation or concerns. Ask ONE question at a time, conversational "
    "and brief (1-2 sentences), building naturally on what they've already said. Never ask "
    "about a topic you already have a clear answer for. Once you have a clear read on all "
    "five topics (usually after 4-6 exchanges), reply with a short closing line thanking "
    "them and telling them you'll follow up with next steps -- do not ask another question "
    "at that point. Never state a specific dollar figure yourself; just draw out enough "
    "about their budget to judge fit."
)


def start_qualifier(lead):
    """Returns the qualifier's opening question. Deterministic (no Claude
    call) — the opener doesn't need generation, and keeping it deterministic
    means the qualifier flow is exercisable with zero ANTHROPIC_API_KEY
    calls until the lead actually starts talking."""
    first_name = _first_name(lead.name)
    return (
        f"Hi {first_name} — before we get you on the calendar, I want to make sure we point "
        f"you at the right next step. Mind if I ask a few quick questions? First: what's "
        f"going on right now that made you look into RPSAS?"
    )


def qualifier_reply(lead, transcript):
    """transcript: list of {"role": "lead"|"assistant", "text": str} in
    order, including the latest lead turn that triggered this reply.
    Returns the qualifier's next line."""
    messages = [
        {"role": "assistant" if t["role"] == "assistant" else "user", "content": t["text"]}
        for t in transcript
    ]
    resp = claude_client.client().messages.create(
        model=claude_client.model(),
        max_tokens=MAX_TOKENS_REPLY,
        system=QUALIFIER_SYSTEM,
        messages=messages,
    )
    return _text(resp)


COMPLETE_SCHEMA = {
    "type": "object",
    "properties": {
        "brief": {"type": "string"},
        "budget_fit_10k_plus": {"type": "boolean"},
        # Claude's structured-output schema validator rejects a
        # ["string","null"] union combined with null in an enum -- empty
        # string is the "no recommendation" sentinel instead, converted
        # back to None in Python below (same convention as patient_sim
        # .SCORE_SCHEMA's named_p_after_shift).
        "recommended_package": {"type": "string", "enum": list(SELF_SERVE_PACKAGES) + [""]},
    },
    "required": ["brief", "budget_fit_10k_plus", "recommended_package"],
    "additionalProperties": False,
}


def complete_qualifier(session):
    """session: a QualifierSession. Returns the parsed verdict dict:
    {"brief": str, "budget_fit_10k_plus": bool, "recommended_package": str|None}.
    `recommended_package` is always None when budget_fit_10k_plus is True
    (booking path), and always a valid SELF_SERVE_PACKAGES key when it's
    False (self-serve checkout path) — enforced here defensively, not just
    trusted from the model."""
    transcript_text = "\n".join(
        f"{'LEAD' if t['role'] == 'lead' else 'QUALIFIER'}: {t['text']}" for t in session.transcript
    )
    prompt = (
        "You are wrapping up a pre-call qualifier conversation for RPSAS, a physician-"
        "communication coaching company. Read the transcript below and produce: a one-"
        "paragraph brief (3-5 sentences) summarizing the lead's situation, timeline, "
        "budget signal, decision-making authority, and any objections, written for the "
        "founder to skim before a call; a verdict on whether this lead can plausibly invest "
        "$10,000 or more (founder-led coaching starts around there); and, only when that "
        "verdict is false, which self-serve package fits best -- 'rpsas_taste' (a low-cost "
        "taste of the method, good default when unsure) or 'applicant_cohort_seat' (better "
        "for a residency/fellowship applicant on a tighter budget and timeline). Leave "
        "recommended_package as an empty string when the budget verdict is true.\n\n"
        f"TRANSCRIPT:\n{transcript_text}"
    )
    resp = claude_client.client().messages.create(
        model=claude_client.model(),
        max_tokens=MAX_TOKENS_COMPLETE,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": COMPLETE_SCHEMA}},
    )
    data = json.loads(_text(resp))
    data["recommended_package"] = data.get("recommended_package") or None

    if data["budget_fit_10k_plus"]:
        data["recommended_package"] = None
    elif data["recommended_package"] not in SELF_SERVE_PACKAGES:
        data["recommended_package"] = "rpsas_taste"  # safe default self-serve option

    return data


def _text(resp):
    return next(b.text for b in resp.content if b.type == "text").strip()
