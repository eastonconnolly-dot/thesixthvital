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
