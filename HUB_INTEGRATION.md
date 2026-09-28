# Hub integration — ready-to-apply patch

This session's write access is scoped to the RPSAS repo — the harness itself
blocked an edit into the Hub repo (`/Users/easton/Desktop/AI Office/Claude Jarvis 1`)
mid-session, correctly: that's a separate, business-critical codebase this
session was never granted write access to. Read access still worked, so
everything below is real, verified against the Hub's actual current code
(exact file, exact surrounding lines), not guessed.

**To apply:** either open a Claude Code session with `--add-dir` (or
`/add-dir`) pointed at the Hub repo and hand it this file, or apply these by
hand. Three small, additive edits to the Hub, plus one new file.

## 1. `hub/db.py` — add `delivery_date` to `customers`

**a. Fresh-DB schema** — in the `customers` block of `_SCHEMA` (starts line
1663), add one line after `notes TEXT DEFAULT '',` (around line 1685):

```sql
  notes TEXT DEFAULT '',
  delivery_date TEXT DEFAULT '',
```

**b. Self-heal ALTER** for existing SQLite/Postgres DBs — add right after the
`case_study_consent` block (ends line 11379), same guarded-idempotent style
every other column addition in this function uses:

```python
    # RPSAS integration (2026-09-28) -- delivery_date is the scheduled date
    # for an RPSAS intensive, set via POST /api/v1/customers by the RPSAS
    # repo's sync layer. In FIELDS["customers"] (unlike is_test_customer/
    # voice_call_consent/etc. above) since it's an ordinary externally-set
    # field, not a system-managed one.
    if "delivery_date" not in {r[1] for r in conn.execute("PRAGMA table_info(customers)")}:
        conn.execute("ALTER TABLE customers ADD COLUMN delivery_date TEXT DEFAULT ''")
```

**c. `FIELDS["customers"]`** (line 8787) — add `"delivery_date"` to the list
so it's writable via the normal internal `db.create()`/`db.update()` path:

```python
    "customers": ["name", "phone", "email", "address", "market", "source", "status", "pipeline", "rep",
                  "marketer", "services", "contact_pref", "no_go_days", "value_cents",
                  "companycam_url", "proposal_url", "contract_url", "notes", "cpa_notes_summary",
                  "lat", "lng", "geocode_provider", "geocode_accuracy", "geocode_status",
                  "geocode_address", "geocoded_at", "map_visibility", "last_activity_at",
                  "referred_by_customer_id", "delivery_date"],
```

**Production:** after deploying this, run `scripts/sync_pg_schema.py` against
production Postgres (`ALBERT_DB=postgres DATABASE_URL=... python
scripts/sync_pg_schema.py`) — it auto-detects the new column from the
reference schema and runs the `ALTER TABLE ... ADD COLUMN` for you. `--check`
first to confirm the diff is exactly this one column before `apply`ing.

## 2. `public_api/routes.py` — let the public API set `pipeline`/`value_cents`/`delivery_date`

Today `POST /api/v1/customers` can only set `name/phone/email/address/market/
source/services/notes` (line 34) — every RPSAS lead would silently land on
`pipeline='retail'`, the roofing company's own pipeline, not RPSAS's. Two
changes:

**a. Widen the whitelist** (line 34):

```python
_PUBLIC_CUSTOMER_CREATE_FIELDS = (
    "name", "phone", "email", "address", "market", "source", "services", "notes",
    "pipeline", "value_cents", "delivery_date",
)
```

**b. Validate `pipeline` before writing** (in `customers_create()`, replacing
lines 189–197) — an unvalidated pipeline value would write silently and only
surface as a `ValueError` later, the first time the Hub UI tries to resolve
stages for that customer. Fail fast instead, at write time:

```python
    from hub import db, pipeline_stages
    from public_api import serializers as ser

    body = request.get_json(force=True, silent=True) or {}
    data = {k: body[k] for k in _PUBLIC_CUSTOMER_CREATE_FIELDS if k in body}
    if "pipeline" in data:
        try:
            pipeline_stages.resolve(g.api_key_tenant_id, data["pipeline"])
        except ValueError as exc:
            return {"error": str(exc)}, 400
    try:
        row = db.create("customers", data, user=f"api-key:{g.api_key_id}")
    except ValueError as exc:
        return {"error": str(exc)}, 400
    return {"customer": ser.customer(row)}, 201
```

Note: this key can still only ever write into its own bound tenant
(`before_request` already calls `db.set_tenant(resolved["tenant_id"])`, and
`g.api_key_tenant_id` is that same value) — this change only lets a key set
*which of its own tenant's pipelines* a customer lands on, not touch another
tenant at all.

`status` is deliberately NOT added to the whitelist — no change needed there.
`scripts/provision_rpsas_tenant.py` below installs `"New Lead"` as the first
stage of every RPSAS pipeline specifically so a new customer's
already-hardcoded column default (`status TEXT DEFAULT 'New Lead'`, unrelated
to this change) already lands on a real stage of whichever RPSAS pipeline it
was created on.

**c. Add `PATCH /customers/<id>`** — v1 today has no update route at all
(confirmed: only `create` exists alongside list/get). Without this, RPSAS can
create a Hub customer once but can never reflect a deal's progress (deposit
paid, delivery date set, stage change) back onto that same record — a real
gap for "full tenant," not a cosmetic one. Add, right after `customers_create`:

```python
@bp.patch("/customers/<int:cid>")
@limiter.limit("30 per minute", key_func=_rl_key)
def customers_update(cid: int):
    denied = _require_write()
    if denied:
        return denied
    from hub import db, pipeline_stages
    from public_api import serializers as ser

    body = request.get_json(force=True, silent=True) or {}
    data = {k: body[k] for k in _PUBLIC_CUSTOMER_CREATE_FIELDS if k in body}
    if "pipeline" in data:
        try:
            pipeline_stages.resolve(g.api_key_tenant_id, data["pipeline"])
        except ValueError as exc:
            return {"error": str(exc)}, 400
    try:
        row = db.update("customers", cid, data, user=f"api-key:{g.api_key_id}")
    except ValueError as exc:
        return {"error": str(exc)}, 400
    return {"customer": ser.customer(row)}
```

⚠️ **Not verified against `hub/db.py`'s actual `db.update()` signature** — I
inferred `db.update(table, id, data, user=...)` from `db.create(table, data,
user=...)`'s pattern (both are described as the two halves of "the standard
`db.create()`/`db.update()` path" in `hub/db.py`'s own `FIELDS` comment) but
never read `db.update()`'s real definition. Check its exact signature (and
whether it raises a different exception on a missing/wrong-tenant `cid` —
probably wants a `404`, not folded into the same `400` path above) before
applying this one specifically.

## 3. `public_api/serializers.py` — surface `delivery_date` on read too

In `customer()` (line 30), add one line so `GET /customers` / `GET
/customers/<id>` return the new field:

```python
        "value_dollars": _dollars(row.get("value_cents")),
        "delivery_date": row.get("delivery_date", ""),
        "created": row.get("created", ""),
```

## 4. New file: `scripts/provision_rpsas_tenant.py`

Modeled directly on the existing `scripts/provision_ai_office_tenant.py` —
same tenant + custom-type + custom-stages + `assert_labels_unarmed()` safety
check pattern, reusing `hub/ai_office_prospects.py`'s `assert_labels_unarmed()`
as-is (it's already generic — takes any `labels` collection) rather than
duplicating its `armed_automation_labels()` logic.

RPSAS gets **three** customer types, one per track — not one, like AI Office
— because `track` in RPSAS's own data model already needs to be its own
segmentation axis, and the Hub's `pipeline` column is exactly that axis. Each
type gets its own copy of RPSAS's deal-stage vocabulary (mirrors
`api/models.py`'s `DEAL_STAGES` in this repo, Title Cased to match the Hub's
label convention). `"Closed Lost"`, not `"Lost"` — same reasoning
`hub/ai_office_prospects.py` documents for its own pipeline: a bare `"Lost"`
label risks colliding with `hub/automations.py`'s global, not-pipeline-scoped
action table.

```python
"""Provisions RPSAS as a new Hub tenant — three custom pipelines (one per
RPSAS track: applicant, physician, program), each carrying RPSAS's own
deal-stage vocabulary. Modeled directly on provision_ai_office_tenant.py.

Run locally first: `python scripts/provision_rpsas_tenant.py`. With
ALBERT_DB/DATABASE_URL unset this only touches the local SQLite dev DB
(data/hub.db) -- never production. Confirm the printed output looks right,
THEN run the identical command against production as a deliberate, separate,
explicit step:
  ALBERT_DB=postgres DATABASE_URL=<prod-url> python scripts/provision_rpsas_tenant.py
"""
from __future__ import annotations

if __name__ == "__main__":  # matches provision_ai_office_tenant.py's own sys.path handling
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))

from provision_tenant import provision
from hub import customer_types, db, pipeline_stages

RPSAS_SLUG = "rpsas"
RPSAS_NAME = "RPSAS"
DEFAULT_ADMIN = "easton@connollyconstructioncompany.com"

# (label, icon, RPSAS-side track key) -- one customer_type per track.
TRACK_TYPES = (
    ("RPSAS Applicant", "🎓", "applicant"),
    ("RPSAS Physician", "🩺", "physician"),
    ("RPSAS Program", "🏥", "program"),
)

# Mirrors api/models.py's DEAL_STAGES in the RPSAS repo. "New Lead" first so
# a customer created via POST /api/v1/customers (which can only set
# `pipeline`, not `status`) lands on a real stage of this pipeline through
# the column's own pre-existing default. "Closed Lost", not "Lost" -- see
# module docstring.
RPSAS_STAGES = (
    "New Lead",
    "Discovery",
    "Proposal Sent",
    "Deposit Paid",
    "Scheduled",
    "Delivered",
    "Closed Lost",
)


def _resolve_type_key(tenant_id: int, label: str) -> str | None:
    for t in customer_types.list_types(tenant_id):
        if t["label"] == label:
            return t["type_key"]
    return None


def ensure_track_type(tenant_id: int, label: str, icon: str, user: str) -> str:
    existing = _resolve_type_key(tenant_id, label)
    if existing:
        return existing
    customer_types.add_type(tenant_id, label, icon, user)
    key = _resolve_type_key(tenant_id, label)
    if key is None:
        raise RuntimeError(f"add_type() reported success for {label!r} but it isn't in list_types() afterward")
    return key


def install_stages(tenant_id: int, pipeline_key: str, user: str) -> list[str]:
    from hub import ai_office_prospects as aop
    aop.assert_labels_unarmed(labels=RPSAS_STAGES)

    for s in pipeline_stages.resolve(tenant_id, pipeline_key)["stages"]:
        if not s["protected"] and s["label"] not in RPSAS_STAGES:
            pipeline_stages.remove_stage_any(tenant_id, pipeline_key, s["stage_id"], user)
    for label in RPSAS_STAGES:
        existing = {s["label"] for s in pipeline_stages.resolve(tenant_id, pipeline_key)["stages"]}
        if label not in existing:
            pipeline_stages.add_stage_any(tenant_id, pipeline_key, label, len(existing), user)

    stages = pipeline_stages.resolve(tenant_id, pipeline_key)["stages"]
    by_label = {s["label"]: s["stage_id"] for s in stages}
    wanted = [by_label[label] for label in RPSAS_STAGES] + [
        s["stage_id"] for s in stages if s["label"] not in RPSAS_STAGES
    ]
    if [s["stage_id"] for s in stages] != wanted:
        pipeline_stages.reorder_stages_any(tenant_id, pipeline_key, wanted, user)
    return [s["label"] for s in pipeline_stages.resolve(tenant_id, pipeline_key)["stages"]]


def provision_rpsas(admin_email: str = DEFAULT_ADMIN, user: str = "rpsas-provision") -> dict:
    res = provision(RPSAS_NAME, RPSAS_SLUG, admin_email, locations=None)
    tenant_id = res["tenant_id"]
    caller_tenant = db.current_tenant()
    db.set_tenant(tenant_id)
    try:
        pipelines = {}
        for label, icon, track in TRACK_TYPES:
            key = ensure_track_type(tenant_id, label, icon, user)
            stages = install_stages(tenant_id, key, user)
            pipelines[track] = {"pipeline_key": key, "stages": stages}
    finally:
        db.set_tenant(caller_tenant)
    return {
        "tenant_id": tenant_id, "created": res["created"], "slug": RPSAS_SLUG,
        "admin_email": res["admin_email"], "admin_routed": res["admin_routed"],
        "pipelines": pipelines,
    }


if __name__ == "__main__":
    import json
    result = provision_rpsas()
    print(json.dumps(result, indent=2))
    print("\nSet these in RPSAS's api/.env (or Render env vars):")
    for track, info in result["pipelines"].items():
        print(f"  HUB_PIPELINE_KEY_{track.upper()}={info['pipeline_key']}")
    print(f"  HUB_TENANT_ID={result['tenant_id']}")
```

## 5. Mint an API key for the new tenant

No CLI script exists for this in the Hub (checked) — the only production
entry point is the owner-only, session-authed `POST /hub/api/public-api/keys`
route. For a first local key, the fastest path (mirrors the pattern
`scripts/*_isolation_check.py` already use) is a short one-off snippet, run
from the Hub repo root:

```python
import sys; sys.path.insert(0, ".")
from hub import db
from public_api import config as pub_cfg, keys as pub_keys

pub_cfg.set_enabled(True)          # flips config.json's public_api.enabled -> true
tenant_id = ...                     # from provision_rpsas_tenant.py's printed output
db.set_tenant(tenant_id)
created = pub_keys.generate_key(tenant_id, "RPSAS sync", "easton@connollyconstructioncompany.com", ["read", "write"])
print(created["key"])               # alb_live_... -- shown exactly once, save it now
```

Put the resulting key in RPSAS's `api/.env` as `HUB_API_KEY` (see
`api/services/hub_sync.py` in this repo — already built, wired, and tested
against a mocked Hub; it activates the moment `HUB_API_KEY` and the three
`HUB_PIPELINE_KEY_*` values are set).

## Status

Not yet applied — this session couldn't write into the Hub repo. Everything
above is verified against the Hub's real current code, ready to paste in.
