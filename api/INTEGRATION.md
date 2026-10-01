# api/ — Phase 6 integration notes

Each Phase 6 sub-area gets its own section below (added independently by
each engineer working on it in parallel — if you're adding a new section,
append it rather than editing another section, and add a one-line entry to
the blueprint-registration and render.yaml-cron summaries at the top).

## New blueprints not yet registered in `api/app.py`

`api/app.py` is actively worked on elsewhere, so new blueprints below are
wired into `api/tests/conftest.py`'s `app` fixture (guarded, so each
becomes a harmless no-op the moment `app.py` registers it for real) rather
than into `app.py` itself. To make them live, add to `create_app()`
alongside the other blueprint imports/registrations:

```python
from routes.playbook import bp as playbook_bp
from routes.closers import bp as closers_bp
from routes.qualifier import bp as qualifier_bp
from routes.call_intake import bp as call_intake_bp
from routes.intake import bp as intake_bp
from routes.uploads import bp as uploads_bp
from routes.consent import bp as consent_bp
from routes.delivery import bp as delivery_bp
...
app.register_blueprint(playbook_bp)
app.register_blueprint(closers_bp)
app.register_blueprint(qualifier_bp)
app.register_blueprint(call_intake_bp)
app.register_blueprint(intake_bp)
app.register_blueprint(uploads_bp)
app.register_blueprint(consent_bp)
app.register_blueprint(delivery_bp)
```

## render.yaml — additional cron services (Phase 6)

Add under `services:` in the repo-root `render.yaml` (not edited by this
change — see `ops/INTEGRATION.md` for the existing two cron entries and
the same env-var conventions used here):

```yaml
  - type: cron
    name: rpsas-content-inventory-check
    runtime: python
    plan: free
    rootDir: api
    buildCommand: pip install -r requirements.txt
    startCommand: flask check-content-inventory
    schedule: "0 13 * * 1"   # Monday 1pm UTC (weekly) -- adjust for the founder's actual timezone
    envVars:
      - key: DATABASE_URL
        fromDatabase:
          name: rpsas-db
          property: connectionString
      - key: BRAND_NAME
        value: RPSAS
      - key: FOUNDER_EMAIL
        sync: false
      - key: GMAIL_SENDER_EMAIL
        sync: false
      - key: GOOGLE_OAUTH_CLIENT_JSON
        sync: false
      - key: GOOGLE_TOKEN_JSON
        sync: false
      - key: COMPANY_MAILING_ADDRESS
        sync: false
      - key: SITE_BASE_URL
        sync: false
```

Runs weekly rather than daily since content inventory (measured in weeks
of queued LinkedIn posts) doesn't move fast enough day-to-day to need more
frequent checking, and the alert-once-per-drop logic (see below) means a
tighter schedule wouldn't change founder-facing behavior anyway — just
adjust the cron expression if a faster catch-a-drop-sooner cadence is
wanted later.

```yaml
  - type: cron
    name: rpsas-auto-approve-proposals
    runtime: python
    plan: free
    rootDir: api
    buildCommand: pip install -r requirements.txt
    startCommand: flask auto-approve-proposals
    schedule: "*/15 * * * *"   # every 15 min -- 20-30 min is also fine given the 2-hour window
    envVars:
      - key: DATABASE_URL
        fromDatabase:
          name: rpsas-db
          property: connectionString
      - key: BRAND_NAME
        value: RPSAS
      - key: FOUNDER_EMAIL
        sync: false
      - key: GMAIL_SENDER_EMAIL
        sync: false
      - key: GOOGLE_OAUTH_CLIENT_JSON
        sync: false
      - key: GOOGLE_TOKEN_JSON
        sync: false
```

Frequent (every 15-30 min) because the thing it's guarding — "auto-send if
not touched in 2 hours" — is itself a short window; a daily or weekly cron
here would make the 2-hour promise meaningless.

```yaml
  - type: cron
    name: rpsas-delivery-reminders
    runtime: python
    plan: free
    rootDir: api
    buildCommand: pip install -r requirements.txt
    startCommand: flask send-delivery-reminders
    schedule: "0 14 * * *"   # daily, 2pm UTC -- adjust for the founder's actual timezone
    envVars:
      - key: DATABASE_URL
        fromDatabase:
          name: rpsas-db
          property: connectionString
      - key: BRAND_NAME
        value: RPSAS
      - key: FOUNDER_EMAIL
        sync: false
      - key: GMAIL_SENDER_EMAIL
        sync: false
      - key: GOOGLE_OAUTH_CLIENT_JSON
        sync: false
      - key: GOOGLE_TOKEN_JSON
        sync: false
      - key: API_BASE_URL
        sync: false

  - type: cron
    name: rpsas-scheduled-followups
    runtime: python
    plan: free
    rootDir: api
    buildCommand: pip install -r requirements.txt
    startCommand: flask send-scheduled-followups
    schedule: "0 15 * * *"   # daily, 3pm UTC -- adjust for the founder's actual timezone
    envVars:
      - key: DATABASE_URL
        fromDatabase:
          name: rpsas-db
          property: connectionString
      - key: BRAND_NAME
        value: RPSAS
      - key: FOUNDER_EMAIL
        sync: false
      - key: GMAIL_SENDER_EMAIL
        sync: false
      - key: GOOGLE_OAUTH_CLIENT_JSON
        sync: false
      - key: GOOGLE_TOKEN_JSON
        sync: false
      - key: API_BASE_URL
        sync: false
```

Both are daily since neither is time-critical to the hour: a reminder that's
7/3/1 days out, or a 30-day check-in / day-7 referral ask, reads the same to
the recipient whether it goes out at 2pm or 9am. `flask init-db` must have
already run against the target database for either to have tables to query,
same as every other cron entry above.

---

## Content on inventory (`api/services/content_inventory.py`)

Implements the build brief's "Content on inventory, not on the founder":

- `batch_ingest(video_paths)` — thin wrapper around `content/ingest.py`'s
  `ingest(source)`, called once per path (the quarterly 8-video
  batch-filming-day flow). One bad video can't sink the batch: every path
  gets its own try/except, and the returned summary
  (`{"total", "succeeded", "failed", "results": [...]}`) records exactly
  which ones failed and why.
- `days_of_inventory_remaining()` — an **estimate**, not an exact number.
  Formula: count of `ContentItem` rows with `type="linkedin_post"` and
  `status in ("approved", "scheduled")`, divided by the 5-posts/week
  cadence (`count * 7 / 5`). Full caveats are in the function's own
  docstring — it deliberately ignores `content/schedule.py`'s actual push
  history, doesn't count other content types as "inventory", and assumes
  in-order publishing at a fixed cadence.
- `check_inventory_and_alert()` — calls the above; if under 30 days,
  calls `ops.error_alerts.alert()`. Alerts **once per drop-below-30-days
  event**, not on every cron run while inventory stays low — tracked via
  the new `InventoryAlertLog` model (`sent_at`, `days_remaining_at_send`,
  `resolved_at`). An unresolved (`resolved_at is None`) row means "already
  alerted for the current drop"; the next time inventory recovers to
  ≥30 days, that row is marked resolved, re-arming alerting for the next
  drop.
- `ingest_consented_clips()` — the "automatic ingestion of consented clips
  from intensives" integration point. Depends on clip-consent tracking
  from the post-delivery automation work (parallel engineer). As of this
  writing, `models.ConsentRequest` exists (`kind`, `granted`, `approved`,
  `session_id`) but carries **no clip/video file path field yet** — so
  this function queries defensively (`try/except ImportError` on the
  model itself, `hasattr` checks on `kind`/`granted` before filtering, and
  a `getattr` chain over candidate path attribute names:
  `clip_path`, `video_path`, `file_path`, `clip_file_path`,
  `recording_path`) and self-heals the moment either the model or a path
  field on it lands, with no code change required here. See the
  function's own docstring for the full behavior/return-shape contract.

CLI: `flask check-content-inventory` (registered in `api/app.py`'s
`register_cli()` — that one edit to `app.py` is in scope per this change's
brief). Same try/except-then-`ops.error_alerts.alert()`-then-reraise
pattern as `send-digest`/`backup-db`/`tick-sequences`.

New model: `InventoryAlertLog` (`api/models.py`).

## Sales handoff readiness (`api/services/call_playbook.py`, `api/services/closers.py`)

Implements the build brief's "Sales handoff readiness":

- `call_playbook.generate_playbook()` — one Claude call
  (JSON-schema-constrained, same `output_config` convention as
  `services/patient_sim.py`/`content/ingest.py` — no `minimum`/`maximum`
  on integers, no null-in-enum) that reads through transcribed sales-call
  transcripts and produces `{"objections": [{"objection","response"}],
  "close_lines": [str]}`. Requires **10+** transcripts
  (`MIN_TRANSCRIPTS_FOR_PLAYBOOK`) — with fewer, returns a clear
  "not enough data yet (N/10)" result instead of calling Claude on a thin
  sample. Persists one `CallPlaybook` row per generation
  (`generated_at`, `transcript_count`, `objections`, `close_lines`);
  admin can regenerate at will.
  - **Transcript source is a documented integration point, not built
    yet.** As of this writing there is no persisted call-transcript model
    anywhere in this repo (the pre-call-qualifier / call-to-proposal work
    references a call pipeline on `Deal`, e.g. `qualifier_brief`, but
    nothing stores full transcripts). `call_playbook._sales_call_transcripts()`
    tries `from models import SalesCall` inside a `try/except ImportError`
    and no-ops cleanly (clear log message, `generate_playbook()` returns
    `{"available": False, ...}`) until that model exists. Expected shape,
    once it lands: a model with a `.transcript` field (Text or JSON) and
    something identifying it as a sales/discovery call (e.g. a `deal_id`
    FK). The moment a model named `SalesCall` with a `.transcript` field
    exists, this self-heals with no code change here — if the real model
    ends up named differently, update the one `try: from models import
    SalesCall` line in `_sales_call_transcripts()`.
- `closers.sync_commission(deal)` — computes and sets
  `Deal.commission_cents = round(deal.amount_cents * closer.commission_rate)`
  the first time a deal with a `closer_id` is observed at
  `stage in ("deposit_paid", "delivered")` (mirrors `ops/digest.py`'s own
  "closed" definition). Idempotent (never recomputes once set) and never
  speculative (untouched below those stages, and a no-op with no
  `closer_id`). **Wiring gap, same shape as `ops/INTEGRATION.md`'s
  `balance_paid_at` note:** `Deal.stage` flips to `"deposit_paid"` inside
  `api/routes/webhooks.py`, which was out of scope to edit here (another
  engineer is actively working in it). Until that file calls
  `services.closers.sync_commission(deal)` directly at the point it sets
  `stage = "deposit_paid"`, commission is instead caught up lazily at two
  call sites: `POST /admin/deals/<id>/assign-closer` (in case the deal is
  already past that stage when a closer is assigned) and
  `GET /admin/closers/<id>/calendar` (recomputes for every deal of that
  closer on each view). A one-line addition to `webhooks.py` would close
  the gap immediately instead of relying on those two lazy triggers:
  ```python
  if deal.stage in ("discovery", "proposal_sent"):
      deal.stage = "deposit_paid"
      # ...
      from services.closers import sync_commission
      sync_commission(deal)  # <-- add this call after stage flips + commits
  ```

New models: `CallPlaybook`, `Closer` (`api/models.py`). New `Deal` columns:
`closer_id` (FK to `closers.id`, nullable — null = founder-owned),
`commission_cents` (Integer, nullable — only ever set once the triggering
stage is actually reached).

New routes: `api/routes/playbook.py` (Blueprint `playbook`,
`url_prefix="/admin/playbook"` — `GET /admin/playbook`,
`POST /admin/playbook/regenerate`); `api/routes/closers.py` (Blueprint
`closers`, `url_prefix="/admin"` — `GET /admin/closers`,
`POST /admin/closers`, `POST /admin/deals/<id>/assign-closer`,
`GET /admin/closers/<id>/calendar`). Both reuse `admin_required` from
`routes.admin`.

New templates: `api/templates/admin/playbook.html`,
`api/templates/admin/closers.html`, `api/templates/admin/closer_calendar.html`
— all extend `admin/base.html`, matching the existing inline `<style>`
block and CSS variables (`--ink`, `--ivory`, `--amber`, `--muted`,
`--border`). The closer calendar is deliberately just a table of that
closer's assigned deals by `delivery_date` — no real calendar integration,
which is out of scope at this stage of the build.

Tests: `api/tests/test_content_inventory.py`, `api/tests/test_call_playbook.py`,
`api/tests/test_closers.py`. All Claude/subprocess calls are mocked; no
real network calls.

---

## Pre-call qualifier + call-to-proposal (`api/services/qualifier_chat.py`, `api/services/call_to_proposal.py`)

Implements the build brief's two "Founder off the loop" flows:

### AI pre-call qualifier

- `services/qualifier_chat.py` — a short Claude-driven chat, shaped like
  `services/patient_sim.py`'s conversation loop (lead = "user", qualifier =
  "assistant"). `start_qualifier(lead)` is a deterministic opener (no
  Claude call — nothing to generate yet). `qualifier_reply(lead,
  transcript)` is one Claude call per turn, steered by `QUALIFIER_SYSTEM` to
  cover situation/timeline/budget/decision-maker/objections, one question
  at a time, without repeating a topic already answered.
  `complete_qualifier(session)` is one JSON-schema-constrained Claude call
  returning `{"brief", "budget_fit_10k_plus", "recommended_package"}` — same
  schema conventions as `patient_sim.SCORE_SCHEMA` (empty-string sentinel
  for the nullable `recommended_package` instead of a `["string","null"]`
  union+null-enum, which 400s). Post-processed defensively: `budget_fit_
  10k_plus=True` always forces `recommended_package=None`, and a False
  verdict with a missing/invalid package defaults to `"rpsas_taste"`.
- `recommended_package` is constrained to `rpsas_taste` /
  `applicant_cohort_seat` — the two brief-named self-serve options that are
  actual `PACKAGES` keys. "Practice trial" (the brief's third self-serve
  option) is the existing Practice subscription flow
  (`stripe_client.create_subscription_checkout_session`,
  `routes/practice.py`), not a `PACKAGES`-keyed deposit product, so it isn't
  a `recommended_package` value — offer it as a parallel link to
  `/practice/login` on the frontend alongside the checkout link this flow
  returns.
- New model: `QualifierSession` (`lead_id`, `transcript` JSON, `status`,
  `brief`, `budget_fit`, `recommended_package`, `created`, `completed_at`).
  New `Deal` column: `qualifier_brief` (Text) — set on the `Deal` (not just
  the session) once a lead clears the $10k+ bar, so it's visible wherever a
  `Deal` already is, no extra join.
- New routes: `api/routes/qualifier.py` (Blueprint `qualifier`,
  `url_prefix="/qualify"`, **public — no `admin_required`**, this is
  lead-facing): `GET /qualify/<lead_id>` (chat page), `POST
  /qualify/<lead_id>/start`, `POST /qualify/<lead_id>/turn`, `POST
  /qualify/<lead_id>/complete`, `GET /qualify/<lead_id>/slots`, `POST
  /qualify/<lead_id>/book`. A `budget_fit_10k_plus=True` completion opens
  (or reuses the lead's newest non-`closed_lost`) `Deal` at
  `stage="discovery"` with `package="tbd"`/`amount_cents=0` — the founder's
  actual discovery call, then call-to-proposal below, fill those in for
  real. A `False` completion calls
  `stripe_client.create_package_checkout_session()` (new — full-price, not
  deposit, checkout for a `PACKAGES` key) and returns its URL; **no Deal is
  created on this path**.
  - **The founder-never-sees-a-sub-$10k-call guarantee is enforced at
    `/slots` and `/book`**, not just by the frontend: both require the
    lead's *most recent completed* `QualifierSession` to have
    `budget_fit=True` (403 otherwise, including when no session exists at
    all). Booking reuses the exact pattern already live in
    `routes/inbox.py::book_call()` —
    `calendar_client.book_slot(...)` then `lead.status = "booked"` — and
    the booked call itself lives only in Google Calendar (no new "booked
    call" row); a booking failure returns 502 without touching
    `lead.status`.
- Template: `api/templates/qualifier/chat.html`, adapted almost verbatim
  from `api/templates/practice/session.html`'s chat pattern (bubble list +
  textarea + send, polling `fetch()` calls) for lead-facing wording, plus a
  results panel that either lists bookable slots or hands over a checkout
  link.
- **Frontend wiring gap, out of scope:** `POST /apply`
  (`routes/public.py`, out of scope to edit here) already returns
  `{lead_id, qualified}`; whoever owns `site/js/apply.js` should redirect a
  `qualified: true` response to `GET /qualify/<lead_id>` instead of (or
  before) any existing next step. Not wired here since it's outside `api/`
  and outside this change's file list.

### Call-to-proposal

- `services/call_to_proposal.py::extract_call_details(transcript_text)` —
  one JSON-schema-constrained Claude call extracting `package` (enum of
  `PACKAGES` keys), `delivery_date` (empty-string sentinel, parsed to a
  `date` or `None`), `participants` (`[{"name","email"}, ...]`), and
  `special_terms` (free text) from a plain-text call transcript. Pulling
  the actual recording (Google Meet/Quo) is explicitly out of scope per the
  brief — this assumes the transcript text is already in hand (e.g. pasted
  into the admin form below).
- `generate_proposal_from_call(deal, transcript_text)` — runs the
  extraction, then **reuses `admin.py::generate_proposal()`'s exact
  pipeline** (same deliverables-list shape, same
  `stripe_client.create_deposit_checkout_session()`, same
  `render_proposal_pdf()`, same `esign.create_signature_request()`) with
  the extracted fields instead of a founder-filled form. `participants` and
  `special_terms` ride along in `Deal.proposal_context` (already documented
  as "inputs used to render the proposal") and get folded into the PDF's
  deliverables list as extra bullets, rather than changing
  `render_proposal_pdf()`'s signature. **The one difference from
  `generate_proposal()`:** it sets `deal.proposal_pending_review = True` +
  `deal.proposal_pending_since = now` instead of `stage = "proposal_sent"`
  — nothing is activated yet.
- `approve_proposal(deal)` — the only thing that flips
  `stage = "proposal_sent"` and clears the pending flag; a no-op (returns
  `False`) if the deal isn't pending. Called both by the founder's
  one-click approval and by the sweep below.
- `sweep_auto_approve(threshold_hours=2, now=None)` — finds every `Deal`
  still `proposal_pending_review` with `proposal_pending_since` older than
  the threshold and approves it; the brief's "auto-send if not touched in 2
  hours". Pure w.r.t. wall-clock time via the `now` param, so tests fabricate
  a past `proposal_pending_since` instead of sleeping. CLI: `flask
  auto-approve-proposals` (registered in `api/app.py`'s `register_cli()` —
  same try/except-then-`ops.error_alerts.alert()`-then-reraise pattern as
  `send-digest`/`backup-db`/`tick-sequences`). Render cron entry is in the
  top-of-file summary above (every 15 min — the window it's guarding is
  itself only 2 hours, so this needs a tight cadence, unlike the weekly
  content-inventory cron).
- New `Deal` columns: `proposal_pending_review` (Boolean, default `False`),
  `proposal_pending_since` (DateTime, nullable). No separate "call intake"
  model — a call-to-proposal run *is* a `Deal` in the pending-review state;
  the `<id>` in the routes below is the `Deal.id`.
- New routes: `api/routes/call_intake.py` (Blueprint `call_intake`,
  `url_prefix="/admin/calls"`, reuses `admin_required` from
  `routes.admin`): `GET /admin/calls/new` (paste-transcript + pick-lead
  form; an optional `deal_id` field targets an existing open deal instead
  of opening a new one), `POST /admin/calls` (runs extraction + generates
  the pending proposal, redirects to review), `GET
  /admin/calls/<deal_id>/review` (extracted fields + embedded PDF preview
  iframe + approve button, only reachable once `proposal_context` exists),
  `GET /admin/calls/<deal_id>/pdf` (streams the generated PDF for that
  iframe), `POST /admin/calls/<deal_id>/approve`.
- Templates: `api/templates/admin/call_intake.html`,
  `api/templates/admin/call_review.html` — both extend `admin/base.html`,
  matching its existing inline `<style>` block and CSS variables (`--ink`,
  `--ivory`, `--amber`, `--muted`, `--border`) exactly rather than
  redeclaring them.

Tests: `api/tests/test_qualifier.py` (budget-fit branching both directions,
the sub-$10k-lead-never-gets-a-booking-path guarantee at both `/slots` and
`/book`, session idempotency, Deal reuse), `api/tests/test_call_to_proposal.py`
(extraction parsing incl. the blank/invalid-date sentinel, the full
extract→PDF→esign→deposit-link chain, `pending_review`→`approve`, the
2-hour auto-approve sweep with a fabricated `proposal_pending_since`, the
CLI command end-to-end, and the `/admin/calls` route flow). All
Claude/Stripe/Calendar calls are mocked (Stripe additionally exercises its
real no-key stub path, same as `test_admin_proposal.py` — `TestConfig` sets
`STRIPE_SECRET_KEY=""`); no real network calls anywhere in the suite.
`cd api && source .venv/bin/activate && python -m pytest -q` — 196 passed.

---

## Onboarding + post-delivery automation (`api/services/onboarding.py`, `api/services/delivery.py`)

Implements the build brief's "Onboarding automation" and "Post-delivery
automation" sections in full.

### Onboarding automation

- `services/onboarding.py::trigger_onboarding(deal)` — the entry point.
  **Idempotent** via `Deal.onboarding_triggered_at` (new column): a second
  call is a no-op (`{"status": "already_triggered"}`). On the first call it:
  - Sends a welcome email (`gmail_client.send_email`).
  - Creates one `IntakeForm` (new model) and emails its link
    (`GET/POST /intake/<token>`, `api/routes/intake.py`).
  - Creates one `UploadLink` (new model) per known participant. Before
    delivery, the only participant known for certain is the deal's own
    primary contact (`deal.lead`) — a cohort's real roster doesn't exist
    yet, it comes from the sponsor roster form below, submitted later.
    `GET/POST /upload/<token>`, `api/routes/uploads.py`.
  - Books two Google Calendar holds via `calendar_client.book_slot()` (the
    delivery date at 9am, and a 30-days-after-delivery check-in) **if**
    `deal.delivery_date` is set. Best-effort: a Calendar API failure is
    logged and skipped, never blocks the rest of onboarding (same
    "external call never blocks the caller" pattern as
    `services/hub_sync.py`).
  - Submits a badge print order via the new
    `services/print_vendor.py::submit_badge_print_order()` — same
    best-effort treatment.
  - For `program`-track deals only: creates one `CohortRoster` (new model)
    and emails the sponsor its roster/room-AV-checklist link
    (`GET/POST /roster/<token>`, `api/routes/intake.py`).
- `services/onboarding.py::send_delivery_reminders(now=None)` — the
  7/3/1-day-before-delivery reminder chain. **Deliberately not built on
  `outreach/engine/sequences.py`'s `Sequence`/`SequenceStep`/
  `SequenceEnrollment` machinery** even though it exists — see the long
  comment above the function for the three reasons (delay_days counts
  forward not backward from a fixed date; `tick()`'s daily-cap throttle and
  suppression-list skip are wrong for a transactional post-signing
  reminder; enrollment is keyed to `Lead` not `Deal`). Instead: three new
  boolean columns on `Deal` (`reminder_7d_sent`, `reminder_3d_sent`,
  `reminder_1d_sent`) plus a direct query, each threshold sent at most
  once. CLI: `flask send-delivery-reminders` (registered in
  `api/app.py`'s `register_cli()` — in scope per this change's brief;
  same try/except-then-`ops.error_alerts.alert()`-then-reraise pattern as
  `send-digest`/`backup-db`/`tick-sequences`). Render cron entry is in the
  top-of-file summary above.

New models (`api/models.py`, all additive): `IntakeForm`, `UploadLink`,
`CohortRoster`. New `Deal` columns: `onboarding_triggered_at` (DateTime,
nullable), `reminder_7d_sent`/`reminder_3d_sent`/`reminder_1d_sent`
(Boolean, default `False`).

New routes: `api/routes/intake.py` (Blueprint `intake`, no prefix — public,
no `admin_required`): `GET/POST /intake/<token>`, `GET/POST
/roster/<token>`. `api/routes/uploads.py` (Blueprint `uploads`, no prefix,
public): `GET/POST /upload/<token>`.

**Upload security** (public, unauthenticated endpoint): extension allowlist
(`UPLOAD_ALLOWED_EXTENSIONS` — mp4/mov/webm, `api/config.py`), a byte-size
cap (`UPLOAD_MAX_BYTES`, default 500MB), and the storage filename is always
`<token>.<ext>` — the client-supplied filename is never used for the path.
**Local disk storage only** (`UPLOAD_STORAGE_DIR`, `api/config.py`) — the
brief calls for "S3-compatible, per-participant links"; swapping
`routes/uploads.py::upload_submit()`'s `file.save(dest_path)` for an S3
`put_object` call (boto3 is already a dependency via `ops/backup.py`'s S3
path) is the one piece of this left for later, flagged rather than built
speculatively. `UploadLink.file_path` would then hold an S3 key instead of
a local path — no schema change needed for that swap.

New templates: `api/templates/intake/form.html`, `api/templates/intake/roster.html`,
`api/templates/uploads/form.html` — simpler public-facing variant (see
`api/templates/practice/login.html`'s style), not `admin/base.html`-derived
since these are participant/sponsor-facing, not founder-facing.

**Wiring gap, out of scope to close here:** `trigger_onboarding(deal)` must
be called once a `SignatureRequest` reaches `status="signed"`, inside
`api/routes/esign.py::submit_signature()` (out of scope to edit — another
engineer is actively working in that file). A one-line addition closes the
gap:

```python
deal.signed_pdf_data = pdf_buf.getvalue()
db.session.commit()

from services.onboarding import trigger_onboarding  # <-- add this import
trigger_onboarding(deal)                             # <-- add this call, after the commit above
```

Until that lands, onboarding only runs if something else calls
`services.onboarding.trigger_onboarding(deal)` directly (e.g. manually, or
from a future admin action) — nothing currently does.

### Post-delivery automation

- `services/delivery.py::complete_session(deal, session_date, session_type,
  participants)` — "founder taps session complete and uploads the reps."
  **Idempotent** via `Deal.delivered_at` (new column). `participants`:
  `[{"name","email","baseline":{...5 rubric dims...},"final":{...}}, ...]`.
  On the first call:
  - Creates one `EncounterSession` + one `Scorecard` per participant
    (existing models, used exactly as already defined — `lift` computed via
    `shared.rubric.score_lift`).
  - **Practice seat provisioning happens first**, before any email for that
    participant — `magic_link.request_login(email, track=deal.lead.track,
    name=...)` creates the `PracticeUser` (+ underlying `Lead` if new) and
    is used for every subsequent email's CAN-SPAM unsubscribe link
    (`?lead_id=...`), since `routes/unsubscribe.py` (out of scope to edit)
    is lead_id-keyed only. The participant is emailed their sign-in link
    immediately.
  - Renders and emails each participant's scorecard PDF
    (`services/pdf/scorecard.py::render_scorecard_pdf`, reused unmodified)
    as a real email **attachment** — see the `gmail_client.py` change
    below.
  - Renders and emails a cohort PDF (`render_cohort_scorecard_pdf`, reused
    unmodified) to the deal's lead/sponsor whenever there's more than one
    participant. For `program`-track deals specifically, a **second** email
    is sent labeled "program score report & leadership deck": the brief
    allows either a real second executive deck or the same cohort PDF with
    a note that a fuller deck is founder-prepared — this implementation
    takes the latter (documented here rather than building a distinct
    executive-deck renderer, which is out of scope).
  - Creates two `ConsentRequest` rows (new model) per participant —
    `kind="testimonial"` and `kind="clip_consent"` — and emails each a
    respond link (`GET/POST /consent/<token>`, `api/routes/consent.py`).
  - Schedules (doesn't send yet) the 30-day check-in confirmation and the
    day-7 referral ask via two new `ScheduledFollowup` rows (new model),
    swept later by `send_scheduled_followups()` below.
  - Sets `deal.stage = "delivered"` and `deal.delivered_at`.
- `services/delivery.py::send_scheduled_followups(now=None)` — sends
  whatever's due (`ScheduledFollowup.sent_at is None and due_at <= now`):
  the 30-day check-in confirmation email, and the day-7 referral-ask email
  containing a `/refer/<lead_id>` link (`api/routes/consent.py`, records a
  `ReferralClick` — new model — on each visit, no attribution beyond "someone
  clicked" per the brief's stated scope). CLI: `flask
  send-scheduled-followups` (registered in `api/app.py`'s `register_cli()`
  — in scope per this change's brief; same
  try/except-then-`ops.error_alerts.alert()`-then-reraise pattern as the
  other scheduled commands). Render cron entry is in the top-of-file
  summary above.

New models (`api/models.py`, all additive): `ConsentRequest`
(`scorecard_id`, `session_id`, `kind` [`testimonial`|`clip_consent`],
`participant_name`, `participant_email`, `token`, `responded_at`,
`response_text`, `granted`, `approved`, `created`), `ScheduledFollowup`
(`deal_id`, `lead_id`, `kind` [`checkin_30day`|`referral_ask`], `due_at`,
`sent_at`, `created`), `ReferralClick` (`lead_id`, `created`). New `Deal`
column: `delivered_at` (DateTime, nullable — idempotency guard for
`complete_session()`).

New routes: `api/routes/delivery.py` (Blueprint `delivery`,
`url_prefix="/admin/delivery"`, reuses `admin_required` from
`routes.admin`): `GET /admin/delivery/<deal_id>` (score-entry form, or a
read-only summary + testimonial-approval table once delivered), `POST
/admin/delivery/<deal_id>/complete`, `POST
/admin/delivery/testimonials/<id>/approve` (flips `ConsentRequest.approved`
— only when `kind="testimonial"` and `granted=True`). `api/routes/consent.py`
(Blueprint `consent`, no prefix, public): `GET/POST /consent/<token>`,
`GET /refer/<int:lead_id>`.

New templates: `api/templates/admin/delivery.html` (extends
`admin/base.html`, matching its inline `<style>` block and CSS variables
exactly), `api/templates/consent/respond.html`,
`api/templates/consent/refer.html` (simpler public-facing variant, matching
`api/templates/practice/login.html`'s style).

**`services/gmail_client.py` change (in scope — it's a service, not a
route):** `send_email()` gained an optional `attachments=None` kwarg (list
of `(filename, bytes, mimetype)` tuples) so scorecard/cohort PDFs can ride
along as real email attachments instead of only a download link. Fully
backward-compatible — every existing call site (`outreach/engine/sequences.py`,
`ops/digest.py`, `ops/error_alerts.py`, `routes/inbox.py`,
`content/send_newsletter.py`) is unaffected: omitting `attachments` keeps
the message a plain `MIMEText`, switching to `MIMEMultipart` only when
attachments are actually passed.

**Wiring gap — closed.** "Positive testimonials flow to the Proof page
automatically after founder approval" is fully wired end to end:
`routes/public.py::public_proof()` queries
`ConsentRequest.query.filter_by(kind="testimonial", granted=True, approved=True)`
and the site's `js/proof.js` renders whatever that returns. The full loop
— session complete → testimonial ask auto-emailed → participant responds
with real text → founder approves on that deal's `/admin/delivery/<id>`
page → live on the public Proof page — has no remaining gap.

New config (`api/config.py` / `.env.example`): `UPLOAD_STORAGE_DIR`
(default `api/uploads/`), `UPLOAD_MAX_BYTES` (default 500MB),
`UPLOAD_ALLOWED_EXTENSIONS` (`{"mp4","mov","webm"}`, not env-configurable —
a fixed allowlist), `PRINT_VENDOR_EMAIL`.

`api/services/print_vendor.py` — the "local vendor's email order" option
from the brief (no print-vendor API key needed): composes a participant
name/quantity table and emails it to `PRINT_VENDOR_EMAIL` via
`gmail_client.send_email`. A real API-based vendor (Printful/Gelato) is
documented as a future swap in the module's own docstring, not built.

Tests: `api/tests/test_onboarding.py` (idempotency, calendar-hold booking +
best-effort failure survival, program-track roster creation, the 7/3/1
reminder thresholds and no-resend behavior), `api/tests/test_intake.py`
(intake form + roster token round-trips, 404/409 on unknown/reused tokens),
`api/tests/test_uploads.py` (valid upload, disallowed extension, oversized
file, missing file, reused token, 404 on unknown token, storage filename
never derived from the client filename), `api/tests/test_delivery.py`
(scorecard + Practice-seat + consent-request creation, idempotency, cohort
vs. single-participant PDF branching, the program-track leadership-deck
email, the admin form/complete/approve routes, the consent respond routes,
the referral-click route, the scheduled-followup sweep sending only what's
due and never resending). All Gmail/Calendar/print-vendor calls are mocked;
no real network access anywhere in the suite.
`cd api && source .venv/bin/activate && python -m pytest -q` — 280 passed.
