from unittest.mock import patch

from extensions import db
from models import Closer, Deal, Lead


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
        deal = Deal.query.get(deal_id)
        assert deal.balance_paid is True
        assert deal.balance_paid_at is not None


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


def test_stripe_webhook_deposit_syncs_closer_commission(app, client):
    with app.app_context():
        closer = Closer(name="Sam Rivera", email="sam@example.com", commission_rate=0.1)
        db.session.add(closer)
        db.session.flush()
        deal = _make_deal(app)
        deal.closer_id = closer.id
        db.session.commit()
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
        assert deal.commission_cents == round(deal.amount_cents * 0.1)


def test_stripe_webhook_deposit_survives_commission_sync_failure(app, client):
    with app.app_context():
        deal = _make_deal(app)
        deal_id = deal.id

    fake_event = {
        "type": "checkout.session.completed",
        "data": {"object": {"metadata": {"deal_id": str(deal_id), "kind": "deposit"}}},
    }
    with patch("routes.webhooks.stripe_client.verify_and_parse_webhook", return_value=fake_event), \
         patch("services.closers.sync_commission", side_effect=RuntimeError("boom")):
        resp = client.post("/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "t=1,v1=fake"})

    assert resp.status_code == 200
    with app.app_context():
        assert Deal.query.get(deal_id).deposit_paid is True


def test_stripe_webhook_package_checkout_creates_paid_deal(app, client):
    with app.app_context():
        lead = Lead(name="Priya Nair", email="priya@example.com", track="applicant")
        db.session.add(lead)
        db.session.commit()
        lead_id = lead.id

    fake_event = {
        "type": "checkout.session.completed",
        "data": {"object": {"metadata": {"lead_id": str(lead_id), "package": "rpsas_taste", "kind": "package"}}},
    }
    with patch("routes.webhooks.stripe_client.verify_and_parse_webhook", return_value=fake_event), \
         patch("services.onboarding.trigger_onboarding") as mock_onboarding:
        resp = client.post("/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "t=1,v1=fake"})

    assert resp.status_code == 200
    with app.app_context():
        deal = Deal.query.filter_by(lead_id=lead_id).first()
        assert deal is not None
        assert deal.package == "rpsas_taste"
        assert deal.amount_cents == 150000
        assert deal.deposit_paid is True
        assert deal.balance_paid is True
        assert deal.balance_due_cents == 0
        assert deal.stage == "deposit_paid"
        assert Lead.query.get(lead_id).status == "customer"
    mock_onboarding.assert_called_once()


def test_stripe_webhook_package_checkout_survives_onboarding_failure(app, client):
    with app.app_context():
        lead = Lead(name="Priya Nair", email="priya@example.com", track="applicant")
        db.session.add(lead)
        db.session.commit()
        lead_id = lead.id

    fake_event = {
        "type": "checkout.session.completed",
        "data": {"object": {"metadata": {"lead_id": str(lead_id), "package": "rpsas_taste", "kind": "package"}}},
    }
    with patch("routes.webhooks.stripe_client.verify_and_parse_webhook", return_value=fake_event), \
         patch("services.onboarding.trigger_onboarding", side_effect=RuntimeError("gmail down")):
        resp = client.post("/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "t=1,v1=fake"})

    assert resp.status_code == 200
    with app.app_context():
        assert Deal.query.filter_by(lead_id=lead_id).first() is not None


def test_stripe_webhook_package_checkout_ignores_unknown_package(app, client):
    with app.app_context():
        lead = Lead(name="Priya Nair", email="priya@example.com", track="applicant")
        db.session.add(lead)
        db.session.commit()
        lead_id = lead.id

    fake_event = {
        "type": "checkout.session.completed",
        "data": {"object": {"metadata": {"lead_id": str(lead_id), "package": "not_a_real_package", "kind": "package"}}},
    }
    with patch("routes.webhooks.stripe_client.verify_and_parse_webhook", return_value=fake_event):
        resp = client.post("/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "t=1,v1=fake"})

    assert resp.status_code == 200
    with app.app_context():
        assert Deal.query.filter_by(lead_id=lead_id).first() is None
