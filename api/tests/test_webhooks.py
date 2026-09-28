import json
from unittest.mock import patch

from extensions import db
from models import Deal, Lead


def _make_deal(app):
    lead = Lead(name="Dana Ortiz", email="dana@example.com", track="physician")
    db.session.add(lead)
    db.session.flush()
    deal = Deal(lead_id=lead.id, package="physician_private", amount_cents=1250000,
                balance_due_cents=1250000, stage="proposal_sent")
    db.session.add(deal)
    db.session.commit()
    return deal


def test_stripe_webhook_marks_deposit_paid(app, client):
    with app.app_context():
        deal = _make_deal(app)
        deal_id = deal.id

    fake_event = {
        "type": "checkout.session.completed",
        "data": {"object": {"metadata": {"deal_id": str(deal_id), "kind": "deposit"}}},
    }
    with patch("routes.webhooks.stripe_client.verify_and_parse_webhook", return_value=fake_event):
        resp = client.post("/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "t=1,v1=fake"})

    assert resp.status_code == 200
    with app.app_context():
        deal = Deal.query.get(deal_id)
        assert deal.deposit_paid is True
        assert deal.deposit_paid_at is not None
        assert deal.stage == "deposit_paid"
        assert deal.balance_due_cents == deal.amount_cents - round(deal.amount_cents * 0.5)


def test_stripe_webhook_marks_balance_paid(app, client):
    with app.app_context():
        deal = _make_deal(app)
        deal.deposit_paid = True
        deal.stage = "deposit_paid"
        db.session.commit()
        deal_id = deal.id

    fake_event = {
        "type": "checkout.session.completed",
        "data": {"object": {"metadata": {"deal_id": str(deal_id), "kind": "balance"}}},
    }
    with patch("routes.webhooks.stripe_client.verify_and_parse_webhook", return_value=fake_event):
        resp = client.post("/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "t=1,v1=fake"})

    assert resp.status_code == 200
    with app.app_context():
        assert Deal.query.get(deal_id).balance_paid is True


def test_stripe_webhook_rejects_bad_signature(app, client):
    with patch("routes.webhooks.stripe_client.verify_and_parse_webhook", side_effect=Exception("bad sig")):
        resp = client.post("/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "garbage"})
    assert resp.status_code == 400


def test_stripe_webhook_ignores_unrelated_event_types(app, client):
    with app.app_context():
        deal = _make_deal(app)
        deal_id = deal.id

    fake_event = {"type": "customer.created", "data": {"object": {}}}
    with patch("routes.webhooks.stripe_client.verify_and_parse_webhook", return_value=fake_event):
        resp = client.post("/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "t=1,v1=fake"})

    assert resp.status_code == 200
    with app.app_context():
        assert Deal.query.get(deal_id).deposit_paid is False


def test_signwell_webhook_marks_signed_in_test_mode(app, client):
    with app.app_context():
        deal = _make_deal(app)
        deal.signwell_envelope_id = "test-envelope-deal-1"
        db.session.commit()
        deal_id = deal.id
        envelope_id = deal.signwell_envelope_id

    payload = {"event": {"type": "document_completed"}, "data": {"object": {"id": envelope_id}}}
    resp = client.post("/webhooks/signwell", data=json.dumps(payload), content_type="application/json")

    assert resp.status_code == 200
    with app.app_context():
        assert Deal.query.get(deal_id).signed_at is not None


def test_signwell_webhook_rejects_when_secret_configured_and_missing_header(app, client):
    app.config["SIGNWELL_WEBHOOK_SECRET"] = "shh"
    app.config["SIGNWELL_TEST_MODE"] = False
    resp = client.post("/webhooks/signwell", data=json.dumps({"event": {"type": "document_completed"}}),
                        content_type="application/json")
    assert resp.status_code == 400
