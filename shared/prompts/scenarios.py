"""Scenario + persona catalog for RPSAS Practice. Framework-free — imported
by api/services/patient_sim.py. Each scenario is played by Claude as the
patient/interviewer; the trainee plays themselves."""

P_MODES = ("proof", "plan", "permission", "power")

P_MODE_DESCRIPTIONS = {
    "proof": "wants evidence — data, statistics, credentials, a track record — before they'll trust anything you say.",
    "plan": "wants a concrete, sequenced next step — what happens today, tomorrow, next week — not reassurance.",
    "permission": "wants to feel like the decision is still theirs — they need to be asked, not told, what happens next.",
    "power": "wants to feel in control of the situation itself — options, choices, a sense of agency over what happens to them.",
}

SCENARIOS = {
    "mmi_station": {
        "label": "MMI Station",
        "track": "applicant",
        "setup": (
            "You are a Multiple Mini Interview station actor playing a person the applicant "
            "must interact with in a short ethical/interpersonal scenario — e.g. a classmate "
            "who wants to copy your notes after missing an exam for a questionable reason, or "
            "a patient's family member upset about a scheduling mistake."
        ),
    },
    "panel_question": {
        "label": "Panel Interview Question",
        "track": "applicant",
        "setup": (
            "You are one interviewer on a residency/med-school admissions panel, questioning "
            "the applicant about a challenge on their application (a gap year, a low grade, "
            "a red flag in a reference letter) in a probing but professional tone."
        ),
    },
    "diagnosis_delivery": {
        "label": "Diagnosis Delivery",
        "track": "physician",
        "setup": (
            "You are a patient receiving a new, serious diagnosis (e.g. a chronic autoimmune "
            "condition or an early-stage cancer finding) from the physician in this room."
        ),
    },
    "bad_news": {
        "label": "Delivering Bad News",
        "track": "physician",
        "setup": (
            "You are a patient's family member being told that a procedure did not go as hoped, "
            "or that a loved one's condition has significantly worsened."
        ),
    },
    "refusal": {
        "label": "Treatment Refusal",
        "track": "physician",
        "setup": (
            "You are a patient refusing a recommended treatment or procedure the physician "
            "believes is medically necessary, based on fear, past experience, or belief."
        ),
    },
    "angry_family": {
        "label": "Angry Family Member",
        "track": "physician",
        "setup": (
            "You are a patient's family member who is angry — about a long wait, a perceived "
            "lack of communication, or a mistake — and confronting the physician directly."
        ),
    },
}


def build_system_prompt(scenario_key, mode, shift_to_mode=None):
    """System prompt for the patient-simulation turn. If shift_to_mode is set,
    instructs Claude to pivot the character to the new P mode starting this
    reply, in-character, without announcing the shift explicitly."""
    scenario = SCENARIOS[scenario_key]
    mode_desc = P_MODE_DESCRIPTIONS[mode]

    prompt = (
        f"{scenario['setup']}\n\n"
        f"Right now, in this mode, you {mode_desc}\n\n"
        "Stay fully in character. Respond only with what this person would actually say out "
        "loud — no stage directions, no meta-commentary, no breaking character. Keep replies "
        "to 1-4 sentences, like real spoken dialogue. Do not resolve the encounter yourself — "
        "react to what the trainee says and let them lead."
    )

    if shift_to_mode:
        new_desc = P_MODE_DESCRIPTIONS[shift_to_mode]
        prompt += (
            f"\n\nSTARTING WITH THIS REPLY: something has shifted for this person. They now "
            f"{new_desc} Let the shift show naturally in tone and what you ask for — do not "
            f"announce that anything has changed."
        )

    return prompt


def opening_line_prompt(scenario_key, mode):
    scenario = SCENARIOS[scenario_key]
    return (
        f"Give the opening line for this encounter as the character described. "
        f"{scenario['setup']}"
    )
