# Integrating the outreach engine (Phase 2)

`outreach/` (list builders + sequence engine) is standalone — nothing in
`api/app.py` was touched (another engineer is actively working in/around
it). Two small new Flask blueprints need registering, plus one CLI/cron
entry point to wire up.

## 1. Register the blueprints in `api/app.py`

Add alongside the other blueprint imports/registrations:

```python
from routes.inbox import bp as inbox_bp
from routes.unsubscribe import bp as unsubscribe_bp
...
app.register_blueprint(inbox_bp)
app.register_blueprint(unsubscribe_bp)
```

Until this is added, `api/tests/test_inbox.py` and
`api/tests/test_unsubscribe.py` each register their blueprint onto the
shared `app` fixture themselves (guarded so it becomes a no-op the moment
`app.py` also registers it), so both route sets are fully exercised today
even though they aren't wired into the real running app yet.

## 2. New models in `api/models.py` (already added — reused as-is elsewhere)

- `SuppressedEmail` (`email`, `reason`, `created`) — the bounce/unsubscribe
  suppression list. `content/send_newsletter.py` (Phase 3, written in
  parallel) already queries this same table/shape, so no coordination
  needed there.
- `MessageDraft` (`message_id` FK → `Message`, `positive`, `confidence`,
  `draft_body`, `proposed_slots` JSON, `approved`, `dismissed`, `created`)
  — the classifier's output, read by `api/routes/inbox.py`.

## 3. Config additions (already made — `api/config.py`, `.env.example`)

```python
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
HUNTER_API_KEY = os.environ.get("HUNTER_API_KEY", "")
APOLLO_API_KEY = os.environ.get("APOLLO_API_KEY", "")
SEQUENCE_DAILY_SEND_CAP = int(os.environ.get("SEQUENCE_DAILY_SEND_CAP", "80"))
SEQUENCE_RAMP_DAYS = int(os.environ.get("SEQUENCE_RAMP_DAYS", "14"))
SEQUENCE_RAMP_START_CAP = int(os.environ.get("SEQUENCE_RAMP_START_CAP", "10"))
```

## 4. Cron wiring (not built here — scheduler infra is out of scope)

Three functions need to run on a schedule (Render cron job / APScheduler /
whatever Ops (Phase 5) sets up):

```python
from outreach.engine import sequences, reply_detection

sequences.tick()                  # advance due sequence enrollments (sends)
reply_detection.check_for_replies()  # poll Gmail threads for replies (also
                                      # best-effort triggers the classifier)
```

`outreach/seed_templates.py` is a one-time (or re-run-when-copy-changes)
step, not a cron job: `python outreach/seed_templates.py` from the repo
root, or call `seed()` directly from inside an app context. It supersedes
`flask seed-sequences`' placeholder `"[TODO: ...]"` bodies with real copy —
safe to run even if `flask seed-sequences` already ran, since it updates
existing per-track sequences in place rather than duplicating them.

## What each piece does

- `outreach/builders/{nppes,hcahps,acgme,places,advisors}.py` — five list
  builders, each returning plain "candidate" dicts (see
  `outreach/builders/__init__.py`'s docstring for the shape). None of them
  write to the database.
- `outreach/builders/enrich.py` — the only place candidates become `Lead`
  rows: Hunter/Apollo email finding, within-batch dedup, suppression-list
  and existing-`Lead` dedup, then `Lead.query`/`db.session` writes.
- `outreach/engine/sequences.py` — `tick()`, the cron advancer (send,
  advance, throttle to 80/day ramped over 14 days, SMS gating).
- `outreach/engine/reply_detection.py` — polls Gmail threads, flags
  replies, stops enrollments, records the reply as an inbound `Message`.
- `outreach/engine/classifier.py` — Claude-based positive/negative
  classification + drafted reply + proposed call slots, written to
  `MessageDraft`.
- `api/routes/inbox.py` + `api/templates/admin/inbox.html` — the admin
  screen: list replies, one-click send/book/dismiss.
- `api/routes/unsubscribe.py` — the actual landing page for the
  `{API_BASE_URL}/unsubscribe?lead_id=...` link every outbound send embeds
  (`gmail_client`'s CAN-SPAM footer) — writes to `SuppressedEmail`, stops
  active enrollments. Nothing served this link before this file existed.

## What's fully live vs. what needs a key this environment doesn't have

Fully live, no key required, verified against the real endpoints while
building this:
- `outreach/builders/nppes.py` — NPPES NPI Registry (public, no auth).
- `outreach/builders/hcahps.py` — CMS Provider Data API (public, no auth).
- `outreach/builders/acgme.py` — CSV-import fallback by design; see its
  module docstring for why ADS Public has no real API worth scraping.

Needs your own key before it does anything live (all gracefully no-op —
clear log message, empty result — when unset; exercised in tests with the
HTTP layer mocked):
- `GOOGLE_PLACES_API_KEY` — `outreach/builders/places.py`.
- `HUNTER_API_KEY` / `APOLLO_API_KEY` — email finding/verification in
  `outreach/builders/enrich.py`. Without either, a candidate that arrived
  without an email (which is most of NPPES/HCAHPS/Places) can never become
  a `Lead` — it's counted under `skipped_no_email`, not silently dropped.
- `ANTHROPIC_API_KEY` — `outreach/engine/classifier.py` (same no-stub-mode
  story as `api/services/patient_sim.py`; `classify_reply()` returns `None`
  cleanly rather than crashing).
- `QUO_API_KEY` — SMS steps in `outreach/engine/sequences.py`; unset means
  every SMS step is skipped (not sent, not an error).
- Gmail/Calendar (`GOOGLE_TOKEN_JSON`) — already a Phase 1 dependency
  (`api/services/gmail_client.py`, `calendar_client.py`); nothing new here,
  but `sequences.tick()`, `reply_detection.py`, and `inbox.py`'s book/send
  routes all depend on it being configured to actually do anything.
- `outreach/builders/advisors.py`'s live scraping path also has no key but
  no guaranteed reliability either — see its module docstring for why the
  CSV-import fallback (`import_advisors_csv`) is the recommended primary
  path for any advisor list that needs to be trustworthy rather than
  best-effort.
