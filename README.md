# RPSAS

Business stack for RPSAS, a physician-communication training company. See
[REUSE.md](REUSE.md) for how this relates to the founder's existing multi-tenant
platform ("the Hub") — short version: the Hub is the CRM/business-ops layer
(leads, deals, pipeline), and RPSAS is provisioned there as a real tenant
(three pipelines, one per track — see [HUB_INTEGRATION.md](HUB_INTEGRATION.md),
not yet applied) via [`api/services/hub_sync.py`](api/services/hub_sync.py).
This repo stays the system of record for everything the Hub has no concept
of — proposals, e-signatures, scorecards, and above all the AI training
platform, RPSAS's actual product differentiator.

## Status

All six phases from the original brief have a working implementation: Sell,
Outreach, Content engine, Practice (the AI training platform — verified live
against a real Anthropic key, not just mocked tests), Ops, and Phase 6
(founder off the loop: pre-call qualifier, call-to-proposal, onboarding,
post-delivery automation, content-on-inventory alerting, sales handoff
readiness) are all built, integrated, and tested — 305 tests,
`cd api && source .venv/bin/activate && python -m pytest`. See each phase's
section below and each module's own `INTEGRATION.md` (`content/`, `ops/`,
`outreach/`, `api/`) for exact status,
what's genuinely live vs. stubbed, and what needs a credential this
environment doesn't have.

`BRAND_NAME` (env var, api-side; `site/js/brand.js` on the static site) controls
every visible instance of the name so it can be renamed in one place.

## Repo layout

```
rpsas/
  site/        static marketing site → GitHub Pages
  api/         Flask app → Render (Postgres)
  outreach/    list builders + sequence engine (Phase 2)
  content/     content engine (Phase 3)
  app/         (unused — Practice is served by api/, one of the two options the brief allowed)
  ops/         dashboards, digest, backups (Phase 5)
  shared/      brand tokens, rubric, prompts, email templates
```

## Phase 1 — Sell (site, application, deposits, proposals, badge)

### Local development

**Site** (static, no build tooling required to view it — `build.js` only
re-syncs brand assets from `shared/`):

```bash
node site/build.js   # copies shared/brand/{tokens.css,badge.svg} into site/
cd site && python3 -m http.server 8010
```

**API**:

```bash
cd api
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env   # then fill in what you have
export FLASK_APP=app.py
flask init-db
flask seed-sequences   # Phase 2's sequences, schema-only for now
flask run --port 5050
```

Visit `http://localhost:8010` for the site and `http://localhost:5050/admin`
for the admin (password from `ADMIN_PASSWORD`).

E-signature is native (no third-party vendor — see "What's genuinely live"
below), so it needs no account or key at all, in dev or production. Without
`STRIPE_SECRET_KEY`, the Stripe client falls back to stub checkout sessions,
so the full apply → qualify → deal → proposal → sign → deposit flow is
exercisable end-to-end with zero external accounts. Google Workspace (Gmail
send, Calendar free/busy) has no stub mode — it needs a real OAuth client and
a one-time `flask google-auth` run — but nothing in Phase 1's request path
depends on it yet (booking-link/nurture sends are wired for Phase 2's
outreach engine, not the synchronous `/apply` request).

### Tests

```bash
cd api && source .venv/bin/activate && python -m pytest
```

38 tests covering the rubric scorer, application-qualification scoring, the
Stripe webhook handler, the native e-sign flow (token issuance, canvas
signature capture, signed-PDF generation, double-sign rejection), PDF
generation (badge/proposal/scorecard all produce valid, non-trivial output),
and `/apply`.

### Deploying

- **Site** → GitHub Pages, serving `site/` as the root. Run `node site/build.js`
  before each deploy (or wire it into your Pages build step) so brand asset
  changes in `shared/` make it into the published site.
- **API** → Render, via [render.yaml](render.yaml). `rootDir: api`. Set the
  `sync: false` env vars in the Render dashboard (Stripe, Google, mailing
  address). Run `flask init-db` once against the new Postgres instance
  (Render shell) before first use.

### What's genuinely live vs. what needs your accounts

Built and tested against stub/sandbox data:
- Application intake + qualification scoring (`POST /apply`)
- Deal → proposal PDF → native e-sign (`/sign/<token>`, canvas signature,
  signed-PDF regeneration) → Stripe deposit checkout (stub) →
  webhook-driven stage transitions
- Badge PDF/PNG, single-participant and cohort scorecard PDFs
- Admin (single-password, server-rendered, no JS framework)

Needs your real credentials before it does anything live:
- `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` (test mode first, obviously)
- Google Workspace OAuth client (`GOOGLE_OAUTH_CLIENT_JSON`) for Gmail send +
  Calendar free/busy — run `flask google-auth` once to mint the token
- A real domain for `SITE_BASE_URL`/`API_BASE_URL` once deployed

E-signature needs no external account at all — it's native (`api/services/esign.py`,
`api/routes/esign.py`), modeled on the Hub's own `hub/esign.py` pattern rather
than a third-party vendor.

### Design template placeholders

`site/privacy.html` and `site/terms.html` are workable starting drafts, not
reviewed by counsel — flagged as such on the pages themselves. The mailing
address/contact email across the site and PDFs are placeholders
(`123 Main Street, Suite 100, Spokane, WA 99201` / `hello@rpsas.example.com`)
— update `COMPANY_MAILING_ADDRESS` and the site's hardcoded footer/contact
strings once you have real ones.

Badge/scorecard/proposal PDFs use ReportLab's built-in Times-Roman/Helvetica
as stand-ins for Playfair Display/Source Sans 3 — no font files were
downloaded without asking first. Drop the real `.ttf` files into
`shared/brand/fonts/` (see `api/services/pdf/base.py` for exact filenames)
to upgrade automatically, no code changes needed.

## Acceptance checklist (Phase 1)

- [x] Submit an application on the site → lead appears in admin
- [x] Qualified path is distinguished from nurture (budget answered + track
      + org present), scored and stored
- [x] Admin can create a deal from a lead and generate a proposal
- [x] Proposal PDF renders; native sign link created, signature captured via
      canvas, signed PDF regenerated with the signature embedded
- [x] Stripe deposit checkout session created (stub verified; live test-mode
      key not exercised here — see above); webhook marks `deposit_paid` and
      advances deal stage
- [x] Badge PDF/PNG render correctly for a given participant
- [x] Scorecard PDF (single + cohort) renders correctly with real rubric data

## Phase 4 — Practice (the AI training platform)

Built ahead of Phases 2/3, at the founder's direction — this is RPSAS's actual
product differentiator (the Hub already solves CRM/pipeline; nothing in the
Hub does simulated-patient training). Lives inside `api/` — magic-link auth,
a Claude-driven patient/interviewer simulation with a randomized mid-encounter
mode shift, rubric scoring, micro-lessons, and Stripe subscriptions with a
7-day trial.

- `shared/prompts/scenarios.py` — the scenario catalog (2 applicant, 4
  physician) and the four P-mode persona descriptions.
- `api/services/patient_sim.py` — drives the Claude conversation (patient
  plays "assistant", trainee plays "user") and the post-encounter scoring
  call (JSON-schema-constrained output, re-validated against
  `shared/rubric.py`).
- `api/services/magic_link.py` — token-based auth (15-minute expiry,
  single-use), modeled on the Hub's own `hub/homeowner_portal.py` pattern.
  Signup and login are the same form; a new email requires a `track`.
- `api/routes/practice.py` + `api/templates/practice/*.html` — dashboard,
  scenario picker, a chat-style encounter page (optional mic input / spoken
  replies via the browser's Web Speech API, no server dependency), and the
  five-lesson unlock progression (`shared/models.py`'s `MICRO_LESSONS` order:
  read → pick → speak → ask → shift — lesson **content** is a placeholder,
  same as the privacy/terms pages, flagged for the founder to fill in).
- Subscriptions: `$49`/`$149`/`$199` (applicant/physician/program-seat),
  7-day trial, via `stripe_client.create_subscription_checkout_session` — set
  `STRIPE_PRICE_*` once those prices exist in the Stripe dashboard.
- Every signup also creates a `Lead` (`source="practice_app"`), per the
  brief's "every user is a lead" requirement — no separate CRM table.

### Local development

Same venv as Phase 1, plus one more env var:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # no stub mode — see below
flask run --port 5050
```

Visit `http://localhost:5050/practice/login`. Without a live Gmail sender
configured, the login endpoint hands the magic link straight back in the
response (`dev_link`) instead of silently doing nothing.

**No stub mode for Claude.** Unlike Stripe (deposit/subscription checkout
both fall back to stub sessions) or e-sign (fully native, no vendor at all),
there's no meaningful fake response for an AI patient conversation — with
`ANTHROPIC_API_KEY` unset, starting a new session returns a clean `503`
rather than crashing, and everything *around* the model call (auth, shift
timing, access gating, scoring persistence, Stripe) is fully tested with the
Claude calls mocked. Conversation quality itself can only be judged with a
real key. Model is `claude-sonnet-5` by default (`CLAUDE_MODEL` env var to
override) — a deliberate cost call for a many-turn-per-session product at
this price point, not a hidden one; bump it if quality doesn't hold up.

### Tests

18 additional tests (56 total) covering magic-link issuance/expiry/single-use,
session creation and access-gating (trial expired → 402, Claude not
configured → 503), the mid-encounter shift firing at the stored turn count
and never re-firing, score persistence, lesson-completion idempotency, and
the subscription webhook updating `subscription_status`.

### Acceptance checklist (Phase 4)

- [x] A user without an account can sign up via magic link and lands on the
      dashboard with a 7-day trial
- [x] Starting an encounter calls the patient simulation and persists the
      opening line
- [x] The mode shift fires at the stored random turn (3-6) and only once
- [x] Ending an encounter scores it on the 5-dimension rubric with quotes,
      names the P mode(s), marks whether the shift was caught, and returns
      one drill
- [x] Micro-lessons unlock in order (read → pick → speak → ask → shift)
- [x] Subscription checkout works (stub verified; real Stripe price ids not
      exercised here) and the webhook updates subscription status
- [x] The signup becomes a `Lead` with `track` and `source="practice_app"`
- [ ] Live conversation quality — needs `ANTHROPIC_API_KEY`, not verifiable here
