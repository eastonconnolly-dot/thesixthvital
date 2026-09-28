# REUSE.md — RPSAS on the Hub

Findings from a targeted, read-only pass over `AI_OFFICE_PATH` = `/Users/easton/Desktop/AI Office/Claude Jarvis 1` ("the Hub"). This repo was **not modified** — every claim below is grounded in the Hub's actual code, not its docs (several of its own docs are stale).

## Headline: the original brief's platform description doesn't match what's actually built

Three of the pieces the build brief said the Hub already has **are not there**:

| Brief assumed | Reality |
|---|---|
| SignWell e-signature | **Not present at all.** No SignWell code or dependency anywhere. E-sign is native: `hub/esign.py` (HMAC token sign-links, canvas capture). SignWell/GHL were evaluated and explicitly rejected (`docs/roadmap/vendor_consolidation_plan_2026-09-06.md`). |
| ReportLab PDF generation | The shipped PDF path is `hub/pdf.py`, built on **xhtml2pdf** (HTML→PDF), not a reusable ReportLab API. One standalone script (`scripts/render_legal_brief_pdf.py`) calls `reportlab` directly but exposes no shared font/color/layout module. |
| Render deployment | **No Render anywhere.** The Hub deploys to a single Hetzner VPS via `gunicorn`+`systemd`+`rsync`. No `render.yaml`, no GitHub Pages pattern for tenant static sites. |

What the brief got right: Postgres RLS-based multi-tenancy, a contact/deal pipeline, a sequence/outreach engine, Stripe, and a scorecard/quiz pattern are all real, working code (details below).

## What's real and reusable as-is

- **Multi-tenant Postgres + RLS** — `hub/db.py`, enforced via `scripts/apply_rls.py`. Every tenant-scoped table must be registered in the `TENANT_TABLES` dict or it gets no isolation.
- **Provisioning a new vertical tenant** — `scripts/provision_ai_office_tenant.py` is a direct template: calls `scripts/provision_tenant.py::provision()`, then `hub/customer_types.py::add_type()` and `hub/pipeline_stages.py::add_stage_any()` to define a new pipeline, and pauses inherited automations.
- **Sequences/outreach** — `hub/campaigns.py` (`marketing_campaigns` / `_steps` / `_enrollments`, `tick()` cron advancer). Every generated send is a **draft**, gated through `albert/backoffice.py`'s human-approval queue before it goes out via `albert/tools/gmail.py` (email) or `albert/tools/sms.py` (SMS, itself gated by opt-out/consent/10DLC checks — note tenant 1's own A2P/10DLC registration is currently *failed*, a live constraint worth budgeting time for).
- **Stripe** — two separate working subsystems: `hub/stripe_payments.py` (customer pays a Hub invoice) and `albert/stripe_billing.py` (tenant pays Hub for SaaS access).
- **Scorecard/quiz pattern** — `static/landing/scorecard.html`: a static, client-side-scored quiz that POSTs a result blob to `web.py`'s `/ai-office/lead`, which becomes a `leads` row. Directly copyable pattern; there's no dedicated scorecard table/model to inherit.
- **Magic-link auth** — `hub/homeowner_portal.py`, reused by `hub/booking.py` and `hub/esign.py`.

## The blocking architectural question

The Hub is a **single Flask monolith** (`web.py`, 12k+ lines) on one VPS. Tenant identity is resolved **per-session** (`tenant_users` membership), not by subdomain or API key routing a request to an isolated app. Its only externally-callable surface is `public_api/routes.py` (`/api/v1/*`), and today that exposes:

- `GET /ping`
- `GET/POST /customers`, `GET /customers/<id>`
- `GET /jobs`, `GET /jobs/<id>`
- `GET /invoices`, `GET /invoices/<id>`
- `GET /proposals`, `GET /proposals/<id>`

**There is no external endpoint to create a proposal, trigger e-sign, create an invoice, or enroll someone in a sequence.** Those actions only exist as internal Hub function calls inside the monolith itself.

That means "RPSAS runs as a new tenant inside the Hub" can mean two genuinely different things, and the repo-layout section of the brief (a separate `rpsas/api/` Flask app, deployed to Render) is only compatible with one of them:

1. **Deep integration** — RPSAS's proposal/esign/invoice/sequence logic is written *inside the Hub repo* (new `hub/` modules, new `web.py` routes, new DB columns on `customers`, a new tenant row), and runs on the Hub's existing Hetzner box. Maximum reuse, zero duplicate CRM — but it means substantial, ongoing write access to a live production monolith, and drops the brief's own repo layout (`rpsas/api/` on Render) entirely.
2. **Standalone-first** — RPSAS gets its own Postgres schema and Flask app in *this* repo, deployed to Render exactly as the brief's repo layout describes, using the Hub only where its read-mostly public API already reaches (e.g. mirroring qualified leads into the Hub's `customers` table via `POST /api/v1/customers` for a unified view). RPSAS keeps its own `leads`/`deals`/`sequences`/e-sign/PDF/Stripe — which duplicates the categories of tables the Hub has, but touches zero Hub code and matches every acceptance criterion and every named tool (SignWell, ReportLab, Render) in the brief's "same stack" paragraph, none of which the Hub actually uses anyway.

Per the brief's own instruction ("if a small extension to the Hub is genuinely required... describe it in REUSE.md and ask before touching it"), I'm flagging this rather than picking one and writing code against it — the two paths produce different repos, and (1) requires commits inside a repo I was told to treat as read-only without sign-off.

## Decision (founder, 2026-09-27)

**Standalone-first.** The Hub is used as a reference implementation to jumpstart RPSAS — its patterns (RLS-lite tenant scoping in `hub/db.py`, the campaign/step/enrollment shape in `hub/campaigns.py`, the magic-link approach in `hub/homeowner_portal.py`/`hub/esign.py`, the HTML-template PDF approach in `hub/pdf.py`) are studied and adapted into this repo's own code. No Hub file is read from or called into at runtime, and nothing here modifies the Hub repo. Hub-mirroring (e.g. pushing qualified leads into the Hub's `customers` via its existing `POST /api/v1/customers`) is left as a later, optional integration, not a Phase 1 dependency.

## What's net-new either way (lives in this repo regardless)

Marketing site, badge/scorecard/proposal PDF templates (no shared ReportLab helper to inherit — building fresh, HTML-template-style like `hub/pdf.py` rather than raw `reportlab.platypus`), the six outreach list builders, the content engine, the Practice app. None of this depends on resolving the question above.

## Update (founder, 2026-09-28): native e-sign, and reopening the CRM question

Two changes from this session:

1. **E-sign switched from SignWell to native**, modeled directly on `hub/esign.py`'s pattern — a per-signer secret token (`secrets.token_urlsafe`), a public `/sign/<token>` page with an HTML5 canvas for the signature, and a regenerated PDF with the captured signature stamped in on submit. `api/services/signwell_client.py` is deleted; see `api/services/esign.py` and `api/routes/esign.py`. No third-party e-sign account, no per-envelope cost.

2. **The founder wants to lean on the Hub as the actual CRM** ("I don't need another CRM, I have the Hub... we could just create a new customized Hub tenant for RPSAS") rather than the standalone-first split decided above. This is a real reversal of that decision, not a refinement of it, so it's being worked through as its own question rather than assumed — see the conversation for the concrete options being weighed (provisioning a real Hub tenant vs. using only the existing `POST /api/v1/customers` from outside, and what a `track`/`package`/`delivery_date` field ends up mapping to on the Hub's `customers` table). Nothing in the Hub has been touched yet.
