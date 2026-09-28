import stripe
from flask import current_app


def _configured():
    return bool(current_app.config["STRIPE_SECRET_KEY"])


def _client():
    stripe.api_key = current_app.config["STRIPE_SECRET_KEY"]
    return stripe


def create_checkout_session(amount_cents, description, success_url, cancel_url, metadata):
    """Returns a dict-like object with at least ["id"] and ["url"]. Falls
    back to stub data when STRIPE_SECRET_KEY isn't configured, so the rest of
    the app (proposal generation, deal state) is exercisable without a live
    Stripe account."""
    if not _configured():
        stub_id = f"cs_test_stub_{metadata.get('kind', 'session')}_{metadata.get('deal_id', '0')}"
        return {"id": stub_id, "url": f"{success_url.split('?')[0]}?stub_checkout={stub_id}"}

    s = _client()
    return s.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "usd",
                "product_data": {"name": description},
                "unit_amount": amount_cents,
            },
            "quantity": 1,
        }],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=metadata,
    )


def create_deposit_checkout_session(deal, package_label, success_url, cancel_url):
    deposit_cents = round(deal.amount_cents * 0.5)
    return create_checkout_session(
        deposit_cents,
        f"{package_label} — Deposit (50%)",
        success_url, cancel_url,
        metadata={"deal_id": str(deal.id), "kind": "deposit"},
    )


def create_balance_checkout_session(deal, package_label, success_url, cancel_url):
    return create_checkout_session(
        deal.balance_due_cents,
        f"{package_label} — Balance",
        success_url, cancel_url,
        metadata={"deal_id": str(deal.id), "kind": "balance"},
    )


def verify_and_parse_webhook(payload, sig_header):
    return stripe.Webhook.construct_event(
        payload, sig_header, current_app.config["STRIPE_WEBHOOK_SECRET"]
    )
