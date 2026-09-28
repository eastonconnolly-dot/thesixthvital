from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from extensions import db
from models import Deal, PracticeUser
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

    event_type = event["type"]
    obj = event["data"]["object"]

    if event_type == "checkout.session.completed":
        metadata = obj.get("metadata", {})
        if "deal_id" in metadata:
            _handle_deal_checkout(metadata, obj)
        elif "practice_user_id" in metadata:
            _handle_subscription_checkout(metadata, obj)

    elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        _handle_subscription_status(obj)

    return jsonify({"received": True})


def _handle_deal_checkout(metadata, session):
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


def _handle_subscription_checkout(metadata, session):
    user = db.session.get(PracticeUser, int(metadata["practice_user_id"]))
    if not user:
        return
    user.stripe_customer_id = session.get("customer")
    user.stripe_subscription_id = session.get("subscription")
    user.subscription_status = "trialing"
    db.session.commit()


def _handle_subscription_status(subscription):
    user = PracticeUser.query.filter_by(stripe_subscription_id=subscription.get("id")).first()
    if not user:
        return
    status = subscription.get("status")  # trialing | active | past_due | canceled | ...
    user.subscription_status = status
    db.session.commit()
