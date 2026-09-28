"""RPSAS Phase 2 — list builders + sequence engine.

Sourcing (`outreach/builders/`) and sending (`outreach/engine/`) are kept
framework-light: builder modules mostly just wrap `requests` calls and don't
import Flask at all unless they need a configured API key from
`current_app.config` (mirroring `api/services/stripe_client.py`'s
`_configured()` house style). Engine modules do need the Flask app (DB
session, config, other `api/services/*` clients), so they defensively add
`api/` to `sys.path` the same way `api/app.py` adds the repo root for
`shared.*` — this lets them be imported either from inside a running Flask
app (routes, CLI) or invoked standalone (a cron script).
"""
