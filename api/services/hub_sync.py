"""Pushes RPSAS leads/deals into the Hub as customer records on RPSAS's own
tenant (see HUB_INTEGRATION.md at the repo root). Fully optional — no-ops
cleanly until HUB_API_KEY and the relevant HUB_PIPELINE_KEY_<TRACK> are set,
which only exist once scripts/provision_rpsas_tenant.py has actually been run
against the Hub.

This app's own leads/deals tables stay the system of record for everything
RPSAS-specific (proposals, e-sign, scorecards, practice sessions) — none of
that exists in the Hub. This is a one-way push so the founder can see RPSAS
leads inside the same CRM as every other business, not a replacement for the
local tables.
"""

import requests
from flask import current_app

from extensions import db


def _configured_for_track(track):
    cfg = current_app.config
    return bool(cfg["HUB_API_KEY"] and cfg["HUB_API_BASE_URL"] and cfg["HUB_PIPELINE_KEYS"].get(track))


def _headers():
    return {"X-Api-Key": current_app.config["HUB_API_KEY"], "Content-Type": "application/json"}


def push_lead(lead):
    """Creates (once) a Hub customer for this lead, on the pipeline matching
    its track. No-ops (returns None) if not configured for that track, or if
    this lead was already pushed. Never raises on a Hub-side failure — logs
    a warning and returns None, since this must never block whatever request
    it's called from (apply, admin deal creation)."""
    if lead.hub_customer_id:
        return None
    if not _configured_for_track(lead.track):
        return None

    cfg = current_app.config
    payload = {
        "name": lead.name,
        "email": lead.email,
        "phone": lead.phone or "",
        "source": lead.source,
        "services": lead.org or "",
        "notes": lead.role or "",
        "pipeline": cfg["HUB_PIPELINE_KEYS"][lead.track],
    }
    try:
        resp = requests.post(
            f"{cfg['HUB_API_BASE_URL']}/api/v1/customers",
            json=payload, headers=_headers(), timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        current_app.logger.warning(f"hub_sync.push_lead failed for lead {lead.id}: {e}")
        return None

    customer = resp.json().get("customer", {})
    lead.hub_customer_id = customer.get("id")
    db.session.commit()
    return customer


def push_deal_update(deal):
    """Updates the Hub customer behind this deal's lead with the deal's
    amount and delivery date. Requires both push_lead() to have already run
    for the lead (needs a hub_customer_id) AND the PATCH /customers/<id>
    endpoint from HUB_INTEGRATION.md section 2c to have been applied — until
    then the Hub returns 404/405, which is treated the same as "not
    configured": logged, never raised."""
    lead = deal.lead
    if not lead or not lead.hub_customer_id:
        return None
    if not _configured_for_track(lead.track):
        return None

    cfg = current_app.config
    payload = {
        "value_cents": deal.amount_cents,
        "delivery_date": deal.delivery_date.isoformat() if deal.delivery_date else "",
    }
    try:
        resp = requests.patch(
            f"{cfg['HUB_API_BASE_URL']}/api/v1/customers/{lead.hub_customer_id}",
            json=payload, headers=_headers(), timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        current_app.logger.warning(f"hub_sync.push_deal_update failed for deal {deal.id}: {e}")
        return None

    return resp.json().get("customer", {})
