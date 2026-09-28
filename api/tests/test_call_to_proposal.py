import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from extensions import db
from models import PACKAGES, Deal, Lead
from services import call_to_proposal


def _fake_claude_response(payload):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(payload))])


def _make_lead_and_deal(app, package="tbd"):
    with app.app_context():
        lead = Lead(name="Priya Shah", email="priya@example.com", track="physician", org="Riverside Health")
        db.session.add(lead)
        db.session.flush()
        deal = Deal(lead_id=lead.id, package=package, amount_cents=0, balance_due_cents=0, stage="discovery")
        db.session.add(deal)
        db.session.commit()
        return lead.id, deal.id


EXTRACTED = {
    "package": "physician_private",
    "delivery_date": date(2026, 11, 15),
    "participants": [{"name": "Priya Shah", "email": "priya@example.com"}],
    "special_terms": "Client requested an evening session.",
}


# ── extract_call_details ───────────────────────────────────────────────

def test_extract_call_details_parses_valid_response(app):
    payload = {
        "package": "physician_private",
        "delivery_date": "2026-11-15",
        "participants": [{"name": "Priya Shah", "email": "priya@example.com"}],
        "special_terms": "Evening session requested.",
    }
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_claude_response(payload)

    with app.app_context():
        with patch("services.call_to_proposal.claude_client.client", return_value=mock_client):
            result = call_to_proposal.extract_call_details("FOUNDER: ... CLIENT: ...")

    assert result["package"] == "physician_private"
    assert result["delivery_date"] == date(2026, 11, 15)
    assert result["participants"] == [{"name": "Priya Shah", "email": "priya@example.com"}]
    assert result["special_terms"] == "Evening session requested."


def test_extract_call_details_blank_or_invalid_date_becomes_none(app):
    payload = {"package": "rpsas_taste", "delivery_date": "", "participants": [], "special_terms": ""}
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _fake_claude_response(payload)

    with app.app_context():
        with patch("services.call_to_proposal.claude_client.client", return_value=mock_client):
            result = call_to_proposal.extract_call_details("transcript")

    assert result["delivery_date"] is None
    assert result["participants"] == []
    assert result["special_terms"] == ""


# ── generate_proposal_from_call: extraction -> PDF -> esign -> deposit ──

def test_generate_proposal_from_call_builds_full_chain_but_stays_pending(app):
    lead_id, deal_id = _make_lead_and_deal(app)

    with app.app_context():
        deal = db.session.get(Deal, deal_id)
        with patch("services.call_to_proposal.extract_call_details", return_value=EXTRACTED):
            result = call_to_proposal.generate_proposal_from_call(deal, "a full transcript")

        assert result["package_label"] == "Physician Private"

        deal = db.session.get(Deal, deal_id)
        assert deal.package == "physician_private"
        assert deal.amount_cents == PACKAGES["physician_private"]["amount_cents"]
        assert deal.delivery_date == date(2026, 11, 15)

        assert deal.proposal_pdf_data is not None
        assert deal.proposal_pdf_data[:5] == b"%PDF-"

        assert len(deal.signature_requests) == 1
        assert deal.signature_requests[0].status == "pending"

        assert deal.deposit_stripe_session_id  # Stripe stub path, TestConfig has no key

        assert deal.proposal_pending_review is True
        assert deal.proposal_pending_since is not None
        # Not activated yet -- stage only flips on approval.
        assert deal.stage == "discovery"

        assert deal.proposal_context["participants"] == EXTRACTED["participants"]
        assert deal.proposal_context["special_terms"] == EXTRACTED["special_terms"]


# ── approve_proposal ────────────────────────────────────────────────────

def test_approve_proposal_activates_a_pending_one(app):
    lead_id, deal_id = _make_lead_and_deal(app)
    with app.app_context():
        deal = db.session.get(Deal, deal_id)
        with patch("services.call_to_proposal.extract_call_details", return_value=EXTRACTED):
            call_to_proposal.generate_proposal_from_call(deal, "transcript")

        deal = db.session.get(Deal, deal_id)
        approved = call_to_proposal.approve_proposal(deal)
        assert approved is True
        assert deal.stage == "proposal_sent"
        assert deal.proposal_pending_review is False
        assert deal.proposal_pending_since is None


def test_approve_proposal_is_a_no_op_when_not_pending(app):
    lead_id, deal_id = _make_lead_and_deal(app)
    with app.app_context():
        deal = db.session.get(Deal, deal_id)
        assert call_to_proposal.approve_proposal(deal) is False
        assert deal.stage == "discovery"


# ── sweep_auto_approve: the 2-hour safety net ───────────────────────────

def test_sweep_auto_approve_only_touches_deals_past_the_threshold(app):
    lead1_id, deal1_id = _make_lead_and_deal(app)
    lead2_id, deal2_id = _make_lead_and_deal(app)

    fixed_now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)

    with app.app_context():
        stale = db.session.get(Deal, deal1_id)
        fresh = db.session.get(Deal, deal2_id)

        with patch("services.call_to_proposal.extract_call_details", return_value=EXTRACTED):
            call_to_proposal.generate_proposal_from_call(stale, "transcript 1")
            call_to_proposal.generate_proposal_from_call(fresh, "transcript 2")

        stale = db.session.get(Deal, deal1_id)
        fresh = db.session.get(Deal, deal2_id)
        stale.proposal_pending_since = fixed_now - timedelta(hours=3)
        fresh.proposal_pending_since = fixed_now - timedelta(minutes=30)
        db.session.commit()

        approved_ids = call_to_proposal.sweep_auto_approve(threshold_hours=2, now=fixed_now)

        assert approved_ids == [deal1_id]

        stale = db.session.get(Deal, deal1_id)
        fresh = db.session.get(Deal, deal2_id)
        assert stale.stage == "proposal_sent"
        assert stale.proposal_pending_review is False
        assert fresh.stage == "discovery"
        assert fresh.proposal_pending_review is True


def test_sweep_auto_approve_cli_command(app):
    lead_id, deal_id = _make_lead_and_deal(app)
    with app.app_context():
        deal = db.session.get(Deal, deal_id)
        with patch("services.call_to_proposal.extract_call_details", return_value=EXTRACTED):
            call_to_proposal.generate_proposal_from_call(deal, "transcript")
        deal = db.session.get(Deal, deal_id)
        deal.proposal_pending_since = datetime.now(timezone.utc) - timedelta(hours=3)
        db.session.commit()

    runner = app.test_cli_runner()
    result = runner.invoke(args=["auto-approve-proposals"])
    assert result.exit_code == 0
    assert str(deal_id) in result.output or "Auto-approved" in result.output

    with app.app_context():
        deal = db.session.get(Deal, deal_id)
        assert deal.stage == "proposal_sent"


# ── admin routes: /admin/calls ──────────────────────────────────────────

def _login(client):
    return client.post("/admin/login", data={"password": "test-admin-password"}, follow_redirects=True)


def test_new_page_requires_admin_login(client):
    resp = client.get("/admin/calls/new")
    assert resp.status_code == 302


def test_create_review_and_approve_flow(app, client):
    with app.app_context():
        lead = Lead(name="Priya Shah", email="priya@example.com", track="physician", org="Riverside Health")
        db.session.add(lead)
        db.session.commit()
        lead_id = lead.id

    _login(client)

    with patch("services.call_to_proposal.extract_call_details", return_value=EXTRACTED):
        resp = client.post("/admin/calls", data={"lead_id": str(lead_id), "transcript": "a full call transcript"})
    assert resp.status_code == 302
    assert "/admin/calls/" in resp.headers["Location"]

    with app.app_context():
        deal = Deal.query.filter_by(lead_id=lead_id).first()
        deal_id = deal.id
        assert deal.proposal_pending_review is True

    review_resp = client.get(f"/admin/calls/{deal_id}/review")
    assert review_resp.status_code == 200
    assert b"Physician Private" in review_resp.data

    pdf_resp = client.get(f"/admin/calls/{deal_id}/pdf")
    assert pdf_resp.status_code == 200
    assert pdf_resp.data[:5] == b"%PDF-"

    approve_resp = client.post(f"/admin/calls/{deal_id}/approve", follow_redirects=True)
    assert approve_resp.status_code == 200

    with app.app_context():
        deal = db.session.get(Deal, deal_id)
        assert deal.stage == "proposal_sent"
        assert deal.proposal_pending_review is False


def test_create_requires_lead_and_transcript(app, client):
    _login(client)
    resp = client.post("/admin/calls", data={"lead_id": "", "transcript": ""})
    assert resp.status_code == 302
    assert "/admin/calls/new" in resp.headers["Location"]
