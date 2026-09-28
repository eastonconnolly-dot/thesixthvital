# RPSAS

Business stack for RPSAS, a physician-communication training company. See
[REUSE.md](REUSE.md) for how this relates to the founder's existing multi-tenant
platform ("the Hub") — short version: standalone, using the Hub as a pattern
reference rather than a runtime dependency.

`BRAND_NAME` (env var, api-side; `site/js/brand.js` on the static site) controls
every visible instance of the name so it can be renamed in one place.

## Repo layout

```
rpsas/
  site/        static marketing site → GitHub Pages
  api/         Flask app → Render (Postgres)
  outreach/    list builders + sequence engine (Phase 2)
  content/     content engine (Phase 3)
  app/         practice app / AI simulated patients (Phase 4)
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

Without `STRIPE_SECRET_KEY` / a live `SIGNWELL_API_KEY`, both clients fall
back to stub responses so the full apply → qualify → deal → proposal →
deposit flow is exercisable end-to-end with zero external accounts. Google
Workspace (Gmail send, Calendar free/busy) has no stub mode — it needs a real
OAuth client and a one-time `flask google-auth` run — but nothing in Phase 1's
request path depends on it yet (booking-link/nurture sends are wired for
Phase 2's outreach engine, not the synchronous `/apply` request).

### Tests

```bash
cd api && source .venv/bin/activate && python -m pytest
```

25 tests covering the rubric scorer, application-qualification scoring, the
Stripe and SignWell webhook handlers, PDF generation (badge/proposal/scorecard
all produce valid, non-trivial output), and `/apply`.

### Deploying

- **Site** → GitHub Pages, serving `site/` as the root. Run `node site/build.js`
  before each deploy (or wire it into your Pages build step) so brand asset
  changes in `shared/` make it into the published site.
- **API** → Render, via [render.yaml](render.yaml). `rootDir: api`. Set the
  `sync: false` env vars in the Render dashboard (Stripe, SignWell, Google,
  mailing address). Run `flask init-db` once against the new Postgres
  instance (Render shell) before first use.

### What's genuinely live vs. what needs your accounts

Built and tested against stub/sandbox data:
- Application intake + qualification scoring (`POST /apply`)
- Deal → proposal PDF → SignWell envelope (test mode) → Stripe deposit
  checkout (stub) → webhook-driven stage transitions
- Badge PDF/PNG, single-participant and cohort scorecard PDFs
- Admin (single-password, server-rendered, no JS framework)

Needs your real credentials before it does anything live:
- `STRIPE_SECRET_KEY`/`STRIPE_WEBHOOK_SECRET` (test mode first, obviously)
- `SIGNWELL_API_KEY` — and per the code comments in
  `api/services/signwell_client.py`, verify the exact request/webhook shape
  against SignWell's current docs before going live; it's modeled on their
  documented v1 API but untested against a real account
- Google Workspace OAuth client (`GOOGLE_OAUTH_CLIENT_JSON`) for Gmail send +
  Calendar free/busy — run `flask google-auth` once to mint the token
- A real domain for `SITE_BASE_URL`/`API_BASE_URL` once deployed

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
- [x] Proposal PDF renders; SignWell envelope created (test mode verified;
      live account not tested — see above)
- [x] Stripe deposit checkout session created (stub verified; live test-mode
      key not exercised here — see above); webhook marks `deposit_paid` and
      advances deal stage
- [x] Badge PDF/PNG render correctly for a given participant
- [x] Scorecard PDF (single + cohort) renders correctly with real rubric data
