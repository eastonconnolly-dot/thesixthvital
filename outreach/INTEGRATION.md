# Integrating the outreach engine (Phase 2)

`outreach/` (list builders + sequence engine). Both blueprints below are
registered in `api/app.py` and the cron entry points are declared in the
repo-root `render.yaml` (`rpsas-sequence-tick`, `rpsas-check-replies`) —
nothing from this section is still open.

## 1. Blueprints in `api/app.py` (done)

For reference, what's registered there:

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

## 4. Cron wiring (done)

`flask tick-sequences` and `flask check-replies` (both registered in
`api/app.py`'s `register_cli()`) wrap `sequences.tick()` and
`reply_detection.check_for_replies()` respectively, and both are
scheduled in the repo-root `render.yaml` (`rpsas-sequence-tick`,
`rpsas-check-replies`, both every 15 minutes) — nothing left to wire up,
just needs the Render deploy itself to exist (see the top "needs you"
card on the work tracker).

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

## Initial targeting criteria (decided 2026-10-01)

First real list-builder batch, once the API is deployed:

**Geography — all three tracks:** Washington, Idaho, Oregon (the founder's
own region). `search_hospitals`/`search_physicians` take `state` as a
single value — call once per state (WA, ID, OR), not a single combined
call.

**Physician track — specialty filter (`taxonomy_description` in
`nppes.search_physicians`):** Oncology, Surgery (general and orthopedic),
Emergency Medicine, Critical Care/ICU, Palliative Care, OB/GYN. Picked for
fit with the business, not availability — these are the specialties where
delivering hard news and holding a room under pressure are routine, daily
pressure, the sharpest match for the pitch. Without a specialty filter,
"all physicians in WA" returns an unfocused list of hundreds of thousands
of providers.

**Exact `taxonomy_description` strings to use (verified live
2026-10-01 — NPPES's matching is specific, generic category names like
"Oncology" alone also return loosely-related non-physician results; see
the credential-filtering fix in `outreach/builders/nppes.py`):**

| Category | Exact string |
|---|---|
| Oncology | `Hematology & Oncology` |
| Surgery (general) | `Surgery` |
| Surgery (orthopedic) | `Orthopaedic Surgery` |
| Emergency Medicine | `Emergency Medicine` |
| Critical Care | `Critical Care Medicine` |
| Palliative Care | `Hospice and Palliative Medicine` |
| OB/GYN | `Obstetrics & Gynecology` |

Live preview run against this exact matrix (WA × ID × OR, physician-only
after the credential fix): ~3,500+ real, named MD/DO physicians, most
state/specialty pairs hitting the 200-result preview cap (so the true
pool is larger). `nppes.py`'s `_to_candidate` now filters to an actual
MD/DO credential — `enumeration_type=NPI-1` covers every individually
enumerated provider, not just physicians, and several of these taxonomy
terms (confirmed live: "Critical Care", "Obstetrics & Gynecology") also
matched nurse practitioners, RNs, and pharmacists before that fix.

**Program track:** `search_hospitals` needs no further filter beyond state
— ready to run the moment the API is deployed. Separately, ACGME's
CSV-import fallback (`import_acgme_csv`) can source residency/fellowship
*programs* specifically (as opposed to hospitals generally) once someone
exports a CSV from ACGME ADS Public's Program Search for WA/ID/OR — not
done yet, no blocker, just hasn't been run.

**Applicant track — target schools (researched and verified live
2026-10-01):** `advisors.py` has no geography filter; it scrapes specific
pre-health advising office pages. Verified these 8 pages are real and
currently live:

| School | Pre-health advising page | Scrapable live? |
|---|---|---|
| University of Washington | https://prehealth.uw.edu/ | Yes |
| Washington State University | https://healthprofessions.wsu.edu/ | Yes |
| Gonzaga University | https://www.gonzaga.edu/student-life/career-services/students/professional-graduate-school-resources/health-professions-pathways-program/gonzaga-pre-health | **No — Cloudflare-protected, blocks automated requests entirely (confirmed via `robots.txt` returning a bot-challenge page). Use `import_advisors_csv` with hand-entered contacts instead.** |
| Portland State University | https://www.pdx.edu/pre-health/contact-pre-health-advising | Yes |
| University of Oregon | https://cas.uoregon.edu/advising/pre-health/connect-pre-health-advising | Yes — 3 named advisors with direct emails already on the page (Sonia Gordillo, Amanda Kong, Camille Hoover) |
| Oregon State University | https://health.oregonstate.edu/academics/pre-health | Yes |
| University of Idaho | https://www.uidaho.edu/current-students/academic-support/academic-advising/pre-health-advising | Yes — 2 named advisors with direct emails already on the page (Natalie Burden, Aubrey Shaw) |
| Idaho State University | https://www.isu.edu/hpac/contact/ | Yes — a full advisor directory with direct emails already on the page |

OHSU was the original idea for an 8th school but isn't a fit — it's a
graduate medical school with no undergraduate pre-health advising office
of its own; swapped for Portland State.
