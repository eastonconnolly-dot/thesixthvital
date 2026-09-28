from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from extensions import db
from models import Deal
from services import signwell_client, stripe_client

bp = Blueprint("webhooks", __name__)


@bp.post("/webhooks/stripe")
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        event = stripe_client.verify_and_parse_webhook(payload, sig_header)
    except Exception as e:
        current_app.logger.warning(f"Stripe webhook signature verification failed: {e}")
        return jsonify({"error": "invalid signature"}), 400

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        metadata = session.get("metadata", {})
        deal_id = metadata.get("deal_id")
        kind = metadata.get("kind")
        deal = db.session.get(Deal, int(deal_id)) if deal_id else None

        if deal and kind == "deposit":
            deal.deposit_paid = True
            deal.deposit_paid_at = datetime.now(timezone.utc)
            deal.balance_due_cents = deal.amount_cents - round(deal.amount_cents * 0.5)
            if deal.stage in ("discovery", "proposal_sent"):
                deal.stage = "deposit_paid"
            db.session.commit()
        elif deal and kind == "balance":
            deal.balance_paid = True
            db.session.commit()

    return jsonify({"received": True})


@bp.post("/webhooks/signwell")
def signwell_webhook():
    payload = request.data
    signature = request.headers.get("X-SignWell-Signature", "")

    if not signwell_client.verify_webhook_signature(payload, signature):
        return jsonify({"error": "invalid signature"}), 400

    event = request.get_json(silent=True) or {}
    event_type = event.get("event", {}).get("type") or event.get("type")
    envelope_id = (
        event.get("data", {}).get("object", {}).get("id")
        or event.get("object", {}).get("id")
    )

    if event_type in ("document_completed", "document.completed") and envelope_id:
        deal = Deal.query.filter_by(signwell_envelope_id=envelope_id).first()
        if deal:
            deal.signed_at = datetime.now(timezone.utc)
            db.session.commit()

    return jsonify({"received": True})
