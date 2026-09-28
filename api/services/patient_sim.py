"""Claude-driven patient/interviewer simulation and post-encounter scoring.
The trainee is "user", the simulated patient is "assistant". A mid-encounter
mode shift is injected after a random 3-6 trainee turns."""

import json
import random

from services import claude_client
from shared.prompts.scenarios import P_MODES, build_system_prompt, opening_line_prompt
from shared.rubric import DIMENSIONS

MAX_TOKENS_REPLY = 300
MAX_TOKENS_SCORE = 1200


def random_shift_turn():
    return random.randint(3, 6)


def random_mode(exclude=None):
    choices = [m for m in P_MODES if m != exclude] if exclude else list(P_MODES)
    return random.choice(choices)


def start_encounter(scenario_key, mode):
    """Returns the patient's opening line."""
    system = build_system_prompt(scenario_key, mode)
    resp = claude_client.client().messages.create(
        model=claude_client.model(),
        max_tokens=MAX_TOKENS_REPLY,
        system=system,
        messages=[{"role": "user", "content": opening_line_prompt(scenario_key, mode)}],
    )
    return _text(resp)


def patient_reply(scenario_key, current_mode, transcript, is_shift_turn, shift_to_mode=None):
    """transcript: list of {"role": "patient"|"trainee", "text": str} in order,
    NOT including the trainee message that triggered this reply's system prompt
    choice — pass the full transcript including the latest trainee turn.
    Returns the patient's next line."""
    system = build_system_prompt(
        scenario_key, current_mode, shift_to_mode=shift_to_mode if is_shift_turn else None
    )
    messages = [
        {"role": "assistant" if t["role"] == "patient" else "user", "content": t["text"]}
        for t in transcript
    ]
    resp = claude_client.client().messages.create(
        model=claude_client.model(),
        max_tokens=MAX_TOKENS_REPLY,
        system=system,
        messages=messages,
    )
    return _text(resp)


SCORE_SCHEMA = {
    "type": "object",
    "properties": {
        "scores": {
            "type": "object",
            # Claude's structured-output schema doesn't support minimum/maximum
            # on integers (400s at request time) -- enum is the supported way
            # to constrain the range. shared.rubric.score_encounter() is the
            # real safety net regardless, re-validating every score below.
            "properties": {d: {"type": "integer", "enum": [1, 2, 3, 4, 5]} for d in DIMENSIONS},
            "required": list(DIMENSIONS),
            "additionalProperties": False,
        },
        "quotes": {
            "type": "object",
            "properties": {d: {"type": "string"} for d in DIMENSIONS},
            "required": list(DIMENSIONS),
            "additionalProperties": False,
        },
        "named_p_initial": {"type": "string", "enum": list(P_MODES)},
        # A ["string","null"] union + enum containing None 400s against
        # Claude's structured-output schema validator ("enum value 'proof'
        # does not match declared type") -- it doesn't support that
        # union-plus-null-in-enum combination. Empty string is the "no
        # shift occurred" sentinel instead; score_encounter() below
        # converts it back to None before returning.
        "named_p_after_shift": {"type": "string", "enum": list(P_MODES) + [""]},
        "shift_caught": {"type": "boolean"},
        "drill": {"type": "string"},
    },
    "required": ["scores", "quotes", "named_p_initial", "shift_caught", "drill"],
    "additionalProperties": False,
}


def score_encounter(scenario_key, transcript, initial_mode, shift_to_mode=None):
    """transcript: list of {"role": "patient"|"trainee", "text": str}.
    Returns the parsed scoring dict validated against SCORE_SCHEMA, with scores
    additionally re-validated against shared.rubric."""
    transcript_text = "\n".join(
        f"{'PATIENT' if t['role'] == 'patient' else 'TRAINEE'}: {t['text']}" for t in transcript
    )
    shift_note = (
        f" Partway through, the patient's mode shifted to '{shift_to_mode}'."
        if shift_to_mode else " The patient's mode did not shift during this encounter."
    )
    prompt = (
        f"You are scoring a communication-training encounter against the RPSAS rubric. "
        f"The patient's initial mode was '{initial_mode}'.{shift_note}\n\n"
        f"Score the TRAINEE's performance (not the patient) on five dimensions, 1-5 each: "
        f"read_accuracy (did they correctly read what the patient wanted), mode_match (did "
        f"their approach match the patient's actual mode), delivery (composure, pacing, "
        f"clarity), adaptation (did they adjust when the patient shifted, if it happened), "
        f"outcome (did the encounter land well). For each dimension, quote the single most "
        f"relevant line from the trainee's side of the transcript. Name which P mode the "
        f"patient was actually in at the start, and after the shift if one occurred (empty "
        f"string for named_p_after_shift if no shift occurred). Mark shift_caught true only "
        f"if a shift occurred AND the trainee visibly "
        f"adapted to it. Give exactly one concrete drill for what to practice next.\n\n"
        f"TRANSCRIPT:\n{transcript_text}"
    )
    resp = claude_client.client().messages.create(
        model=claude_client.model(),
        max_tokens=MAX_TOKENS_SCORE,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": SCORE_SCHEMA}},
    )
    data = json.loads(_text(resp))
    data["named_p_after_shift"] = data.get("named_p_after_shift") or None

    from shared.rubric import score_encounter as validate_scores
    validate_scores(data["scores"])  # raises InvalidScoreError if the model somehow drifts
    return data


def _text(resp):
    return next(b.text for b in resp.content if b.type == "text").strip()
