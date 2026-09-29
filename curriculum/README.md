# Delivery curriculum

Internal only — not published anywhere on the site or in any client-facing
material. This is the actual run-of-show for every paid package: what you
say, what you do, minute by minute, so a real session doesn't require
designing it live.

Every script assumes the RPSAS Method as taught in [`shared/prompts/lessons.py`](../shared/prompts/lessons.py)
(Read → Pick the P → Speak → Ask → Shift) and scores against the same
25-point rubric in [`shared/rubric.py`](../shared/rubric.py) (Read accuracy,
Mode match, Delivery, Adaptation, Outcome — 1–5 each) that the Practice app
and every real Scorecard use. Read those two files first if you haven't —
every script below leans on that shared vocabulary instead of re-explaining
it each time.

## What's in each script

- **Pre-session checklist** — what to have ready, what you should already
  know from the qualifier brief / intake form before the person walks in.
- **Minute-by-minute run-of-show** — timed blocks for the actual session(s).
- **Exact exercises** — the specific drills, prompts, and simulated
  scenarios to run, not just "do a roleplay."
- **Debrief scripts** — the actual questions to ask after each encounter, so
  scoring the rubric happens in the room, not from memory afterward.
- **Close** — what happens in the last 15 minutes, every time: the final
  encounter is filmed, scored, the client leaves knowing their number, and
  you ask for a testimonial on camera while the win is still fresh. The
  automated email consent-request (`services/delivery.py`) goes out either
  way, but an in-person ask converts far better than a cold follow-up —
  don't skip it because the automation exists.

## Before you deliver

- **Dry-run every script once before your first paying client.** None of
  this has been run for real yet. Find a friendly, low-stakes subject — a
  colleague, a friend's kid heading into interviews — and run the actual
  timed script against them first. You're not testing whether the method
  works; you're testing your own pacing and catching the parts that read
  fine on paper and land wrong out loud.
- **Get your own state right before they walk in.** The energy you bring
  into the room is itself part of what you're teaching — Speak's dials
  (pace, eye contact, killing your own fillers) apply to you running the
  session, not just to the person you're training. If you're rushed or
  distracted going in, it shows up in their results.

## If something goes wrong

Script these too, not just the client-facing content — the moment to
figure out what you say is before it happens, not during it.

- **Guarantee not met, client wants a refund instead of the continuation
  session.** The guarantee is continued coaching, not a refund — say that
  plainly, then make the continuation genuinely appealing: "I'd rather get
  you the actual result than hand your money back and have you walk away
  without it. Let's get this scheduled." If they push hard for cash back
  anyway, that's a business call to make in the moment, not a script.
- **No-show.** Have a one-line rebooking message ready to send same-day —
  don't let it sit. Repeated no-shows on a cohort seat are a different
  conversation than a single private-intensive reschedule; decide your own
  policy on refund/reschedule for each before you need it live.
- **Someone's uncomfortable being filmed.** Confirm the filming is for
  their own scorecard, not for marketing, unless they separately opt into
  the consent-request ask at the end — the automated flow already keeps
  those two things separate (`ConsentRequest.kind == "clip_consent"` is its
  own opt-in, distinct from participating in the session itself). Say that
  explicitly if someone hesitates; usually the discomfort is about the
  footage being used publicly, not about being recorded for scoring.
- **A disruptive or checked-out cohort member.** Address it one-on-one at
  the next break, not in front of the group. Ask directly what's going on
  before assuming disengagement — sometimes it's the format, sometimes it's
  unrelated to the room entirely.

## Delivery capacity

Match Ready and Physician Private are two full days of your own time for
$10,000–$12,500. That's real revenue, but it's also a hard ceiling — your
calendar is the constraint on how many of these you can run in a year, not
demand. The cohort/program formats and RPSAS Practice subscriptions are
where volume actually scales without more of your time per dollar. Worth
deciding deliberately how much of the calendar goes to 1:1 intensives
versus cohort/program work as bookings pick up, rather than defaulting to
whichever closes first — and if 1:1 demand outgrows what you can personally
deliver, these scripts are written specifically so a second trained
facilitator could run them consistently, not just so you don't have to
improvise.

## Status

| Package | Track | Format | Status |
|---|---|---|---|
| [Match Ready](match-ready.md) | Applicant | 2-day private, $10,000 | Done |
| [RPSAS Taste](rpsas-taste.md) | Applicant + Physician | 1 session, $1,500 | Done |
| [Applicant Cohort Seat](applicant-cohort-seat.md) | Applicant | 1 day, cohort, $1,800 | Done |
| [Physician Private](physician-private.md) | Physician | 2-day private, $12,500 | Done |
| [20-Hour Block](twenty-hour-block.md) | Physician | Modular, flexible, $7,000 | Done |
| [Program Cohort — 1 Day](program-cohort-1day.md) | Program | 1 day, on-site | Done |
| [Program Cohort — 2 Day](program-cohort-2day.md) | Program | 2 day, on-site | Done |
| [System Series](system-series.md) | Program | Multi-session, academic year | Done |

## Materials common to every in-person session

- Filming setup (phone on a tripod is enough — framing matters more than
  camera quality): wide enough to catch both people, audio clear enough to
  re-watch for filler-word counting.
- Printed rubric card (one per encounter) — see `shared/rubric.py` for the exact
  five dimensions and the 1–5 anchors per point, so scoring is consistent
  session to session instead of a felt sense each time.
- A visible timer for pacing drills — not on your phone (that's also the
  camera); a cheap kitchen timer or a laptop clock works.
- The qualifier brief and (for anything closed via `call_intake.py`) the
  call-to-proposal `special_terms` field, read before the person walks in —
  every script below assumes you already know their situation, timeline,
  and specific hesitations going in, because you do.
