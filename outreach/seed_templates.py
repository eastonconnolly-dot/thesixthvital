"""Seeds the four-touch nurture sequences with real copy, per track.

`api/app.py`'s existing `flask seed-sequences` CLI command creates the
`Sequence`/`SequenceStep` schema with placeholder `"[TODO: ...]"` bodies (see
its docstring: "the schema is created in Phase 1"). This module is that
TODO, filled in -- real subject lines and bodies for the four touches
(demo clip, one-page proof, a free lunch-hour talk invite, break-up),
tailored per track (applicant / physician / program), following the
"T1/T2/T3/T4" structure described in the project README's Phase 2 section.

Run it with `python outreach/seed_templates.py` (creates its own app
context) or call `seed()` directly from inside one (e.g. a test, or a future
Flask CLI command wired up by whoever owns `api/app.py`). It's idempotent
and safe to re-run: it reuses an existing `Sequence` row per track if one is
already there (including one created by the old placeholder
`flask seed-sequences` command) and overwrites its steps' subject/body/delay
rather than creating a duplicate sequence.

`SequenceStep.delay_days` here is the gap since the *previous* step fired,
not a cumulative day-count from enrollment -- see
`outreach/engine/sequences.py`'s module docstring for why. Each track's four
steps use `0, 4, 4, 4`, landing touches at day 0 / day 4 / day 8 / day 12
after enrollment, matching the README's day-0/4/8/12 framing.
"""

import os
import sys

_API_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "api"))
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)


def _step(delay_days, subject, body, channel="email"):
    return {"delay_days": delay_days, "channel": channel, "subject": subject, "body": body}


SEQUENCE_DEFINITIONS = {
    "applicant": {
        "name": "Nurture — Applicant",
        "steps": [
            _step(0,
                "the 90 seconds before your MMI answer matters more than the answer",
                "<p>Hi {{first_name}},</p>"
                "<p>Most applicants prep answers. Almost none prep the first four seconds of "
                "delivering them — and that's what a panel actually remembers.</p>"
                "<p>Here's a 90-second clip of a real applicant's MMI answer, before and after "
                "one Sixth Vital session: <a href=\"https://sixthvital.example.com/proof#mmi-clip\">watch it</a>.</p>"
                "<p>Same content. Completely different read from the panel.</p>"
                "<p>— Sixth Vital</p>"),
            _step(4,
                "the one page admissions coaches keep forwarding to applicants",
                "<p>Hi {{first_name}},</p>"
                "<p>Quick follow-up — here's the one-pager version of what we do: the 5-dimension "
                "rubric we score against, why \"P-mode\" reading (what an interviewer actually "
                "wants from you in the moment) moves scores more than rehearsed content, and "
                "before/after numbers from the last applicant cohort.</p>"
                "<p><a href=\"https://sixthvital.example.com/proof\">One-page proof →</a></p>"
                "<p>— Sixth Vital</p>"),
            _step(4,
                "free 45 minutes on MMI stations, no pitch",
                "<p>Hi {{first_name}},</p>"
                "<p>I'm running a free 45-minute session on how MMI stations actually get scored "
                "and where most applicants lose points without noticing. Open to anyone prepping "
                "for interviews this cycle, no obligation.</p>"
                "<p><a href=\"https://sixthvital.example.com/apply.html#lunch-talk\">Grab a spot →</a></p>"
                "<p>— Sixth Vital</p>"),
            _step(4,
                "closing your file",
                "<p>Hi {{first_name}},</p>"
                "<p>I'll stop filling your inbox here — didn't want to just go quiet. If interview "
                "prep becomes a priority again before your cycle, the door's open; just reply.</p>"
                "<p>Good luck with your applications either way.</p>"
                "<p>— Sixth Vital</p>"),
        ],
    },
    "physician": {
        "name": "Nurture — Physician",
        "steps": [
            _step(0,
                "reading the room before you deliver the diagnosis",
                "<p>Hi Dr. {{first_name}},</p>"
                "<p>Most physicians are taught *what* to say when delivering hard news. Almost "
                "none are taught to read, in real time, whether the person in front of them wants "
                "proof, a plan, permission, or a sense of control — and to adjust mid-conversation "
                "when it shifts, which it usually does.</p>"
                "<p>Here's a short clip from one of our simulated encounters showing that shift "
                "happening live: <a href=\"https://sixthvital.example.com/proof#physician-clip\">watch it</a>.</p>"
                "<p>— Sixth Vital</p>"),
            _step(4,
                "the one-pager physicians forward to their chief residents",
                "<p>Hi Dr. {{first_name}},</p>"
                "<p>Here's the one-page breakdown: the 5-dimension rubric (read accuracy, mode "
                "match, delivery, adaptation, outcome), how the mid-encounter shift training "
                "works, and measured score lift from physicians who've been through it.</p>"
                "<p><a href=\"https://sixthvital.example.com/proof\">One-page proof →</a></p>"
                "<p>— Sixth Vital</p>"),
            _step(4,
                "free 45-minute session: the mid-conversation shift",
                "<p>Hi Dr. {{first_name}},</p>"
                "<p>I'm hosting a free 45-minute lunch-hour session on the single most common "
                "mistake in delivering bad news: missing the moment a patient's family shifts "
                "from wanting proof to wanting control (or vice versa). Open seat if you want it, "
                "no strings attached.</p>"
                "<p><a href=\"https://sixthvital.example.com/method.html#lunch-talk\">Reserve a spot →</a></p>"
                "<p>— Sixth Vital</p>"),
            _step(4,
                "last one from me",
                "<p>Hi Dr. {{first_name}},</p>"
                "<p>I'll let this thread go quiet after this. If a structured way to practice "
                "difficult conversations — with real scoring, not just a workshop — becomes "
                "useful later, just reply and we'll pick it back up.</p>"
                "<p>— Sixth Vital</p>"),
        ],
    },
    "program": {
        "name": "Nurture — Program",
        "steps": [
            _step(0,
                "what your residents' patient conversations actually sound like under pressure",
                "<p>Hi {{first_name}},</p>"
                "<p>{{org}}'s trainees can ace the content of a bad-news conversation and still "
                "lose the patient in the room — because no one taught them to read what the "
                "patient in front of them actually needs in that moment, or to adjust when it "
                "changes mid-conversation.</p>"
                "<p>Here's a 90-second clip showing that gap closing across one cohort: "
                "<a href=\"https://sixthvital.example.com/proof#program-clip\">watch it</a>.</p>"
                "<p>— Sixth Vital</p>"),
            _step(4,
                "the one-pager program directors send up the chain",
                "<p>Hi {{first_name}},</p>"
                "<p>Here's what a cohort intensive with {{org}} would look like on paper: the "
                "5-dimension scoring rubric, baseline-to-final lift data from prior cohorts, and "
                "the format (half-day or full-day, on-site).</p>"
                "<p><a href=\"https://sixthvital.example.com/programs.html\">One-page proof →</a></p>"
                "<p>— Sixth Vital</p>"),
            _step(4,
                "free 45 minutes for your program leads",
                "<p>Hi {{first_name}},</p>"
                "<p>I'm offering a free 45-minute session for {{org}}'s program leadership — "
                "walking through how the rubric scores real encounters and what a cohort's lift "
                "data actually looks like, no sales pitch attached.</p>"
                "<p><a href=\"https://sixthvital.example.com/programs.html#lunch-talk\">Reserve a spot →</a></p>"
                "<p>— Sixth Vital</p>"),
            _step(4,
                "closing the loop with {{org}}",
                "<p>Hi {{first_name}},</p>"
                "<p>I don't want to keep landing in your inbox if the timing's off — I'll stop "
                "here. If a cohort intensive becomes a priority for {{org}} down the line, reply "
                "any time and we'll pick this back up.</p>"
                "<p>— Sixth Vital</p>"),
        ],
    },
    None: {
        "name": "Nurture — all tracks",
        "steps": [
            _step(0,
                "60 seconds of what Sixth Vital actually does",
                "<p>Hi {{first_name}},</p>"
                "<p>Sixth Vital trains physicians and physician-track trainees to read what the person "
                "in front of them actually needs — proof, a plan, permission, or control — and "
                "adjust when it shifts mid-conversation.</p>"
                "<p>Here's a short clip: <a href=\"https://sixthvital.example.com/proof\">watch it</a>.</p>"
                "<p>— Sixth Vital</p>"),
            _step(4,
                "the one-page version",
                "<p>Hi {{first_name}},</p>"
                "<p>Here's a one-pager on the method and the numbers behind it: "
                "<a href=\"https://sixthvital.example.com/proof\">sixthvital.example.com/proof</a>.</p>"
                "<p>— Sixth Vital</p>"),
            _step(4,
                "free 45-minute session, open invite",
                "<p>Hi {{first_name}},</p>"
                "<p>I'm running a free 45-minute session on the framework behind this — open to "
                "anyone, no obligation: "
                "<a href=\"https://sixthvital.example.com/method.html#lunch-talk\">grab a spot</a>.</p>"
                "<p>— Sixth Vital</p>"),
            _step(4,
                "one last note",
                "<p>Hi {{first_name}},</p>"
                "<p>I'll stop here for now — reply any time if this becomes useful later.</p>"
                "<p>— Sixth Vital</p>"),
        ],
    },
}


def seed():
    """Creates or updates one `Sequence` (+ its 4 `SequenceStep`s) per
    track. Reuses an existing sequence for a track if one is already there
    (whether from a prior run of this function or from the old placeholder
    `flask seed-sequences` command) instead of creating a duplicate."""
    from extensions import db
    from models import Sequence, SequenceStep

    created, updated = 0, 0
    for track, definition in SEQUENCE_DEFINITIONS.items():
        sequence = Sequence.query.filter_by(track=track).first()
        if sequence:
            sequence.name = definition["name"]
            sequence.active = True
            updated += 1
        else:
            sequence = Sequence(name=definition["name"], track=track, active=True)
            db.session.add(sequence)
            db.session.flush()  # assign sequence.id for the steps below
            created += 1

        existing_steps = {s.step_order: s for s in sequence.steps}
        for i, step_def in enumerate(definition["steps"]):
            step = existing_steps.get(i)
            if step:
                step.delay_days = step_def["delay_days"]
                step.channel = step_def["channel"]
                step.subject = step_def["subject"]
                step.body = step_def["body"]
            else:
                db.session.add(SequenceStep(
                    sequence_id=sequence.id, step_order=i,
                    delay_days=step_def["delay_days"], channel=step_def["channel"],
                    subject=step_def["subject"], body=step_def["body"],
                ))

    db.session.commit()
    return {"created": created, "updated": updated}


if __name__ == "__main__":
    from app import create_app

    app = create_app()
    with app.app_context():
        result = seed()
        print(f"Seeded outreach sequences: {result['created']} created, {result['updated']} updated.")
