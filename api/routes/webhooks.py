from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from extensions import db
from models import Deal
from services import stripe_client

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
