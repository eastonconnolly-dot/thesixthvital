from unittest.mock import MagicMock, patch

from extensions import db
from models import Deal, Lead
from services import hub_sync


def _make_lead(app, track="physician"):
    lead = Lead(name="Dana Ortiz", email="dana@example.com", track=track, org="Cascade Orthopedics")
    db.session.add(lead)
    db.session.commit()
    return lead


def _configure_hub(app, track="physician"):
    app.config["HUB_API_BASE_URL"] = "https://hub.example.com"
    app.config["HUB_API_KEY"] = "alb_live_test"
    app.config["HUB_PIPELINE_KEYS"] = {"applicant": "", "physician": "rpsas_physician", "program": ""}


def test_push_lead_noop_when_not_configured(app):
    with app.app_context():
        lead = _make_lead(app)
        app.config["HUB_API_KEY"] = ""
        result = hub_sync.push_lead(lead)
        assert result is None
        assert lead.hub_customer_id is None


def test_push_lead_noop_when_track_has_no_pipeline_key(app):
    with app.app_context():
        lead = _make_lead(app, track="applicant")
        _configure_hub(app)  # only physician has a pipeline key configured
        result = hub_sync.push_lead(lead)
        assert result is None
        assert lead.hub_customer_id is None


def test_push_lead_creates_hub_customer_and_stores_id(app):
    with app.app_context():
        lead = _make_lead(app)
        _configure_hub(app)

        fake_resp = MagicMock()
        fake_resp.raise_for_status.return_value = None
        fake_resp.json.return_value = {"customer": {"id": 42, "pipeline": "rpsas_physician"}}

        with patch("services.hub_sync.requests.post", return_value=fake_resp) as mock_post:
            result = hub_sync.push_lead(lead)

        assert result == {"id": 42, "pipeline": "rpsas_physician"}
        assert lead.hub_customer_id == 42
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["json"]["pipeline"] == "rpsas_physician"
        assert call_kwargs["json"]["name"] == "Dana Ortiz"
        assert call_kwargs["headers"]["X-Api-Key"] == "alb_live_test"


def test_push_lead_is_idempotent(app):
    with app.app_context():
        lead = _make_lead(app)
        lead.hub_customer_id = 99
        db.session.commit()
        _configure_hub(app)

        with patch("services.hub_sync.requests.post") as mock_post:
            result = hub_sync.push_lead(lead)

        assert result is None
        mock_post.assert_not_called()


def test_push_lead_swallows_request_errors(app):
    import requests as requests_module

    with app.app_context():
        lead = _make_lead(app)
        _configure_hub(app)

        with patch("services.hub_sync.requests.post", side_effect=requests_module.ConnectionError("down")):
            result = hub_sync.push_lead(lead)

        assert result is None
        assert lead.hub_customer_id is None


def test_push_deal_update_noop_without_hub_customer_id(app):
    with app.app_context():
        lead = _make_lead(app)
        deal = Deal(lead_id=lead.id, package="physician_private", amount_cents=1250000, balance_due_cents=1250000)
        db.session.add(deal)
        db.session.commit()
        _configure_hub(app)

        with patch("services.hub_sync.requests.patch") as mock_patch:
            result = hub_sync.push_deal_update(deal)

        assert result is None
        mock_patch.assert_not_called()


def test_push_deal_update_sends_amount_and_delivery_date(app):
    with app.app_context():
        lead = _make_lead(app)
        lead.hub_customer_id = 42
        deal = Deal(lead_id=lead.id, package="physician_private", amount_cents=1250000,
                    balance_due_cents=1250000)
        from datetime import date
        deal.delivery_date = date(2026, 11, 15)
        db.session.add(deal)
        db.session.commit()
        _configure_hub(app)

        fake_resp = MagicMock()
        fake_resp.raise_for_status.return_value = None
        fake_resp.json.return_value = {"customer": {"id": 42, "value_dollars": 12500.0}}

        with patch("services.hub_sync.requests.patch", return_value=fake_resp) as mock_patch:
            result = hub_sync.push_deal_update(deal)

        assert result == {"id": 42, "value_dollars": 12500.0}
        call_kwargs = mock_patch.call_args.kwargs
        assert call_kwargs["json"]["value_cents"] == 1250000
        assert call_kwargs["json"]["delivery_date"] == "2026-11-15"
        assert "42" in mock_patch.call_args.args[0]
