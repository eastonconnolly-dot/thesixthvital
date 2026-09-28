from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from extensions import db
from models import PACKAGES, Deal, Lead, PracticeUser, ProcessedWebhookEvent
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

    # Stripe's delivery guarantee is at-least-once -- a retried delivery of an
    # event we already handled must not re-run it (most importantly,
    # _handle_package_checkout must not create a second Deal for the same
    # payment). Skip dispatch entirely for an event id we've already recorded.
    event_id = event.get("id")
    if event_id:
        if db.session.get(ProcessedWebhookEvent, event_id):
            return jsonify({"received": True, "duplicate": True})
        db.session.add(ProcessedWebhookEvent(id=event_id))
        db.session.commit()

    event_type = event["type"]
    obj = event["data"]["object"]

    if event_type == "checkout.session.completed":
        metadata = obj.get("metadata", {})
        if "deal_id" in metadata:
            _handle_deal_checkout(metadata, obj)
        elif "practice_user_id" in metadata:
            _handle_subscription_checkout(metadata, obj)
        elif metadata.get("kind") == "package":
            _handle_package_checkout(metadata, obj)

    elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        _handle_subscription_status(obj)

    return jsonify({"received": True})


def _handle_deal_checkout(metadata, session):
    deal_id = metadata.get("deal_id")
    kind = metadata.get("kind")
    try:
        deal = db.session.get(Deal, int(deal_id)) if deal_id else None
    except (TypeError, ValueError):
        current_app.logger.warning(f"deal checkout webhook: non-integer deal_id in metadata: {deal_id!r}")
        return

    if deal and kind == "deposit":
        deal.deposit_paid = True
        deal.deposit_paid_at = datetime.now(timezone.utc)
        deal.balance_due_cents = deal.amount_cents - round(deal.amount_cents * 0.5)
        if deal.stage in ("discovery", "proposal_sent"):
            deal.stage = "deposit_paid"
        db.session.commit()

        try:
            from services.closers import sync_commission
            sync_commission(deal)
        except Exception as e:
            current_app.logger.warning(f"commission sync failed for deal {deal.id}: {e}")
    elif deal and kind == "balance":
        deal.balance_paid = True
        deal.balance_paid_at = datetime.now(timezone.utc)
        db.session.commit()


def _handle_package_checkout(metadata, session):
    """A sub-$10k self-serve purchase from the pre-call qualifier
    (services/qualifier_chat.py -> stripe_client.create_package_checkout_session).
    Full price, paid up front -- no deposit/balance split, no founder call."""
    lead_id = metadata.get("lead_id")
    package_key = metadata.get("package")
    try:
        lead = db.session.get(Lead, int(lead_id)) if lead_id else None
    except (TypeError, ValueError):
        current_app.logger.warning(f"package checkout webhook: non-integer lead_id in metadata: {lead_id!r}")
        return
    package = PACKAGES.get(package_key)
    if not lead or not package:
        return

    deal = Deal(
        lead_id=lead.id, package=package_key, amount_cents=package["amount_cents"],
        balance_due_cents=0, deposit_paid=True, deposit_paid_at=datetime.now(timezone.utc),
        balance_paid=True, balance_paid_at=datetime.now(timezone.utc), stage="deposit_paid",
    )
    db.session.add(deal)
    lead.status = "customer"
    db.session.commit()

    try:
        from services.closers import sync_commission
        sync_commission(deal)
    except Exception as e:
        current_app.logger.warning(f"commission sync failed for self-serve deal {deal.id}: {e}")

    try:
        from services.onboarding import trigger_onboarding
        trigger_onboarding(deal)
    except Exception as e:
        current_app.logger.warning(f"onboarding trigger failed for self-serve deal {deal.id}: {e}")


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
