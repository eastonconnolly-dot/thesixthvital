from unittest.mock import patch

from extensions import db
from models import Deal, Lead


def _login(client):
    return client.post("/admin/login", data={"password": "test-admin-password"}, follow_redirects=True)


def test_generate_proposal_creates_pdf_context_and_sign_request(app, client):
    with app.app_context():
        lead = Lead(name="Dana Ortiz", email="dana@example.com", track="physician", org="Cascade Orthopedics")
        db.session.add(lead)
        db.session.commit()
        lead_id = lead.id

    _login(client)
    client.post(f"/admin/leads/{lead_id}/deals", data={"package": "physician_private", "delivery_date": "2026-11-15"})

    with app.app_context():
        deal = Deal.query.filter_by(lead_id=lead_id).first()
        deal_id = deal.id

    resp = client.post(f"/admin/deals/{deal_id}/proposal", follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        deal = db.session.get(Deal, deal_id)
        assert deal.stage == "proposal_sent"
        assert deal.proposal_pdf_data is not None
        assert deal.proposal_pdf_data[:5] == b"%PDF-"
        assert deal.proposal_context["package_label"] == "Physician Private"
        assert len(deal.signature_requests) == 1
        assert deal.signature_requests[0].status == "pending"


def test_admin_requires_login(client):
    resp = client.get("/admin/")
    assert resp.status_code == 302


def test_set_delivery_date_books_calendar_holds_if_onboarding_already_ran(app, client):
    with app.app_context():
        lead = Lead(name="Priya Nair", email="priya@example.com", track="applicant")
        db.session.add(lead)
        db.session.flush()
        deal = Deal(
            lead_id=lead.id, package="rpsas_taste", amount_cents=150000, balance_due_cents=0,
            stage="deposit_paid",
        )
        db.session.add(deal)
        db.session.commit()
        deal_id = deal.id

        from services.onboarding import trigger_onboarding
        with patch("services.onboarding.gmail_client.send_email", return_value={"message_id": "m", "thread_id": "t"}), \
             patch("services.onboarding.calendar_client.book_slot"), \
             patch("services.onboarding.submit_badge_print_order"):
            result = trigger_onboarding(deal)
        assert result["calendar_holds"] == []  # no delivery_date yet -- the bug this route fixes

    _login(client)
    with patch("services.onboarding.calendar_client.book_slot") as mock_book:
        resp = client.post(f"/admin/deals/{deal_id}/delivery-date", data={"delivery_date": "2026-11-15"}, follow_redirects=True)

    assert resp.status_code == 200
    assert mock_book.call_count == 2  # delivery + 30-day check-in, booked retroactively
    with app.app_context():
        deal = db.session.get(Deal, deal_id)
        assert deal.delivery_date is not None
        assert deal.calendar_holds_booked_at is not None


def test_set_delivery_date_requires_login(client):
    resp = client.post("/admin/deals/1/delivery-date", data={"delivery_date": "2026-11-15"})
    assert resp.status_code == 302
