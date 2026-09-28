# Phase 5 (Ops) — integration notes

What's in `ops/`:

- `ops/digest.py` — Monday 6am weekly metrics digest, emailed to `FOUNDER_EMAIL`.
  `build_digest(as_of=None)` (pure, testable) + `send_digest(as_of=None)`
  (renders + sends). CLI: `flask send-digest`.
- `ops/backup.py` — nightly DB backup (`pg_dump`/sqlite-copy → gzip → GitHub
  release or S3-compatible bucket). `run_backup()`. CLI: `flask backup-db`.
- `ops/error_alerts.py` — `alert(subject, detail)`, a one-function helper any
  module can import to notify `FOUNDER_EMAIL` when something fails silently.

Both CLI commands (registered in `api/app.py`'s `register_cli()`) catch
exceptions, best-effort call `error_alerts.alert()` before re-raising, so a
cron failure both shows up in Render's logs (non-zero exit) *and* emails the
founder directly.

## render.yaml — add these two cron services

Render cron jobs run on their own schedule, independent of the web service,
but need the same `rootDir`/`buildCommand`/env as `rpsas-api`. Add these two
entries under `services:` in the repo-root `render.yaml` (not edited by this
change, since the brief scoped `render.yaml` edits to documentation here
rather than the file itself):

```yaml
  - type: cron
    name: rpsas-weekly-digest
    runtime: python
    plan: free
    rootDir: api
    buildCommand: pip install -r requirements.txt
    startCommand: flask send-digest
    schedule: "0 6 * * 1"   # Monday 6am UTC -- adjust for the founder's actual timezone
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
      - key: STRIPE_PRICE_APPLICANT
        sync: false
      - key: STRIPE_PRICE_PHYSICIAN
        sync: false
      - key: STRIPE_PRICE_PROGRAM_SEAT
        sync: false

  - type: cron
    name: rpsas-nightly-backup
    runtime: python
    plan: free
    rootDir: api
    buildCommand: pip install -r requirements.txt
    startCommand: flask backup-db
    schedule: "0 9 * * *"   # nightly, 9am UTC (~1-2am Pacific) -- adjust as needed
    envVars:
      - key: DATABASE_URL
        fromDatabase:
          name: rpsas-db
          property: connectionString
      - key: BRAND_NAME
        value: RPSAS
      - key: FOUNDER_EMAIL
        sync: false
      - key: GMAIL_SENDER_EMAIL   # only needed so a backup failure can alert()
        sync: false
      - key: GOOGLE_OAUTH_CLIENT_JSON
        sync: false
      - key: GOOGLE_TOKEN_JSON
        sync: false
      # pick ONE backup destination:
      - key: BACKUP_GITHUB_REPO
        sync: false
      - key: AWS_ACCESS_KEY_ID
        sync: false
      - key: AWS_SECRET_ACCESS_KEY
        sync: false
      - key: BACKUP_BUCKET
        sync: false
      - key: BACKUP_S3_ENDPOINT_URL
        sync: false
```

Notes:
- Render's Postgres `pg_dump` client version must be compatible with the
  managed Postgres version; the free Postgres plan doesn't come with shell
  access to install `pg_dump` on the cron job's own container unless the
  buildpack already includes Postgres client tools. If `pg_dump` isn't on
  PATH in Render's python runtime, either switch this cron job's runtime to
  one that includes Postgres client tools, or add a `buildCommand` step that
  installs them (e.g. `apt-get`-based buildpacks aren't available on Render's
  standard python runtime — this may need a Dockerfile-based service instead;
  flagged here rather than assumed, since it's an infra choice, not a code one).
- If `BACKUP_GITHUB_REPO` is used, whatever executes `gh release create` also
  needs `gh` on PATH and pre-authenticated (`gh auth login` with a token that
  has `repo` scope on that private repo) — Render's cron container is
  ephemeral, so this generally means baking a `GH_TOKEN` env var into the
  build (`gh` reads `GH_TOKEN`/`GITHUB_TOKEN` automatically) rather than an
  interactive login. Add a `GH_TOKEN` env var (`sync: false`) if you go this
  route.
- `flask send-digest` and `flask backup-db` both need `flask init-db` to have
  already run against the target database (same as the web service).

## New env vars (add to `.env.example` locally; `sync: false` on Render)

| Var | Used by | Notes |
|---|---|---|
| `FOUNDER_EMAIL` | digest, error_alerts | Destination inbox. No default — both raise a clear `RuntimeError` if unset rather than silently no-op. |
| `BACKUP_GITHUB_REPO` | backup | `"owner/repo"` of a **private** repo to hold backups as release assets. Requires `gh` installed + authenticated wherever `flask backup-db` runs. |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `BACKUP_BUCKET` | backup | S3-compatible upload path (used if `BACKUP_GITHUB_REPO`+`gh` aren't both available). |
| `BACKUP_S3_ENDPOINT_URL` | backup | Optional; set for a non-AWS S3-compatible host (Cloudflare R2, Backblaze B2, etc). Blank = real AWS S3. |

`api/config.py` and `api/.env.example`-equivalent documentation were updated
accordingly (see `api/config.py`'s "Phase 5: ops" section).

## New model field

`Deal.balance_paid_at` (`api/models.py`) — mirrors the existing
`deposit_paid_at`. Needed so `ops/digest.py`'s cash-collected metric can tell
*when* a balance was paid, not just that it was, the same way it already
could for deposits.

**Wiring gap, out of scope for this change:** `api/routes/webhooks.py`'s
`_handle_deal_checkout()` sets `deal.balance_paid = True` on the `kind ==
"balance"` branch but does not yet set `deal.balance_paid_at`. That file was
out of scope here (flagged as actively worked on by another engineer). A
one-line addition closes the gap:

```python
elif deal and kind == "balance":
    deal.balance_paid = True
    deal.balance_paid_at = datetime.now(timezone.utc)  # <-- add this
    db.session.commit()
```

Until that lands, `cash_collected`'s balance-portion will always read as
`$0` in the digest (the deposit-portion already works today, since
`deposit_paid_at` was already being set).

## Known metric limitations (documented in code, repeated here for visibility)

- **`calls_booked`** is a proxy: it counts `Deal` rows *created* in the
  window, not actual booked calls. `Lead.status` can be `"booked"` per the
  state-machine comment in `models.py`, but nothing currently transitions a
  lead into that status, and there's no timestamp to window a query on even
  if it did. Deliberately **not** adding a `Lead.booked_at` field speculatively
  since nothing would ever set it yet — once a real booking flow exists
  (calendar webhook, admin action, etc.), add that timestamp and swap the
  proxy in `ops/digest.py::_period_deal_metrics` for a real query.
- **`weeks_booked`** and **`mrr_cents`** are snapshot metrics (current state,
  not naturally "in a window"). Their week-over-week deltas are
  reconstructed by re-running the same snapshot query as of the prior
  window's boundary, filtered to records that already existed by then
  (`created <= reference`). This is a real approximation, not a stored
  history table — see the docstrings on `_weeks_booked_asof` and
  `_mrr_cents_asof` in `ops/digest.py` for the exact caveats (MRR especially:
  there's no subscription-status-change log, so the "prior" snapshot uses
  each user's *current* `subscription_status`, which misses churn/upgrades
  that happened mid-week).
- **`proposals_out`** / **`closed`** use `Deal.updated` as a proxy for "stage
  changed in this window" (there's no dedicated stage-change log). A deal
  that has sat in a stage for a while but had an unrelated field touched
  this week would be miscounted. Low-risk in practice today (few other
  things update a `Deal` after it settles into a stage) but worth noting.

## Testing

`api/tests/test_ops_digest.py` and `api/tests/test_ops_backup.py` cover all
of the above with hand-built datasets and exact-value assertions (including
window half-open boundaries, MRR track-name mapping, and the
`created`-leakage interaction between `calls_booked` and the `weeks_booked`
test fixtures — see comments in the test file). All external calls
(`gmail_client.send_email`, `subprocess.run` for `pg_dump`/`gh`, `boto3`) are
mocked; no real sends, dumps, or uploads happen in the test suite.
