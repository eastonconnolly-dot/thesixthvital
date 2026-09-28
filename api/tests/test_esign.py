import base64
import io
from unittest.mock import patch

from PIL import Image

from extensions import db
from models import Deal, Lead
from services import esign


def _tiny_png_data_url():
    img = Image.new("RGB", (4, 4), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _make_deal_with_context(app):
    lead = Lead(name="Dana Ortiz", email="dana@example.com", track="physician")
    db.session.add(lead)
    db.session.flush()
    deal = Deal(
        lead_id=lead.id, package="physician_private", amount_cents=1250000,
        balance_due_cents=1250000, stage="proposal_sent",
        proposal_pdf_data=b"%PDF-1.4 stub",
        proposal_context={
            "lead_name": lead.name, "lead_org": lead.org, "package_label": "Physician Private",
            "deliverables": ["Two-day intensive"], "deposit_link": "https://checkout.stripe.com/test/abc",
            "delivery_date": "2026-11-15", "mailing_address": "123 Main St",
        },
    )
    db.session.add(deal)
    db.session.commit()
    return deal


def test_create_signature_request_generates_unique_token(app):
    with app.app_context():
        deal = _make_deal_with_context(app)
        req1, url1 = esign.create_signature_request(deal, "Dana Ortiz", "dana@example.com")
        req2, url2 = esign.create_signature_request(deal, "Dana Ortiz", "dana@example.com")
        assert req1.token != req2.token
        assert req1.token in url1
        assert req1.status == "pending"


def test_sign_page_renders_for_valid_token(app, client):
    with app.app_context():
        deal = _make_deal_with_context(app)
        sig_request, _ = esign.create_signature_request(deal, "Dana Ortiz", "dana@example.com")
        token = sig_request.token

    resp = client.get(f"/sign/{token}")
    assert resp.status_code == 200
    assert b"Physician Private" in resp.data


def test_sign_page_404_for_unknown_token(client):
    resp = client.get("/sign/does-not-exist")
    assert resp.status_code == 404


def test_submit_signature_marks_signed_and_generates_signed_pdf(app, client):
    with app.app_context():
        deal = _make_deal_with_context(app)
        sig_request, _ = esign.create_signature_request(deal, "Dana Ortiz", "dana@example.com")
        token = sig_request.token
        deal_id = deal.id

    resp = client.post(f"/sign/{token}", json={
        "typed_name": "Dana R. Ortiz",
        "agreed": True,
        "signature_png_base64": _tiny_png_data_url(),
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "signed"
    assert data["deposit_link"] == "https://checkout.stripe.com/test/abc"

    with app.app_context():
        deal = db.session.get(Deal, deal_id)
        assert deal.signed_at is not None
        assert deal.signed_pdf_data is not None


def test_submit_signature_triggers_onboarding(app):
    with app.app_context():
        deal = _make_deal_with_context(app)
        sig_request, _ = esign.create_signature_request(deal, "Dana Ortiz", "dana@example.com")
        token = sig_request.token

    with patch("services.onboarding.trigger_onboarding") as mock_trigger:
        resp = app.test_client().post(f"/sign/{token}", json={
            "typed_name": "Dana R. Ortiz", "agreed": True, "signature_png_base64": _tiny_png_data_url(),
        })
    assert resp.status_code == 200
    mock_trigger.assert_called_once()


def test_submit_signature_succeeds_even_if_onboarding_fails(app):
    with app.app_context():
        deal = _make_deal_with_context(app)
        sig_request, _ = esign.create_signature_request(deal, "Dana Ortiz", "dana@example.com")
        token = sig_request.token

    with patch("services.onboarding.trigger_onboarding", side_effect=RuntimeError("gmail not configured")):
        resp = app.test_client().post(f"/sign/{token}", json={
            "typed_name": "Dana R. Ortiz", "agreed": True, "signature_png_base64": _tiny_png_data_url(),
        })
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "signed"


def test_submit_signature_rejects_second_attempt(app, client):
    with app.app_context():
        deal = _make_deal_with_context(app)
        sig_request, _ = esign.create_signature_request(deal, "Dana Ortiz", "dana@example.com")
        token = sig_request.token

    payload = {"typed_name": "Dana Ortiz", "agreed": True, "signature_png_base64": _tiny_png_data_url()}
    first = client.post(f"/sign/{token}", json=payload)
    assert first.status_code == 200
    second = client.post(f"/sign/{token}", json=payload)
    assert second.status_code == 409


def test_submit_signature_requires_all_fields(app, client):
    with app.app_context():
        deal = _make_deal_with_context(app)
        sig_request, _ = esign.create_signature_request(deal, "Dana Ortiz", "dana@example.com")
        token = sig_request.token

    resp = client.post(f"/sign/{token}", json={"typed_name": "Dana Ortiz", "agreed": True})
    assert resp.status_code == 400


def test_submit_signature_rejects_non_png_payload(app, client):
    with app.app_context():
        deal = _make_deal_with_context(app)
        sig_request, _ = esign.create_signature_request(deal, "Dana Ortiz", "dana@example.com")
        token = sig_request.token

    resp = client.post(f"/sign/{token}", json={
        "typed_name": "Dana Ortiz", "agreed": True,
        "signature_png_base64": "data:image/png;base64," + base64.b64encode(b"not a png").decode(),
    })
    assert resp.status_code == 400


def test_document_pdf_serves_unsigned_then_signed(app, client):
    with app.app_context():
        deal = _make_deal_with_context(app)
        sig_request, _ = esign.create_signature_request(deal, "Dana Ortiz", "dana@example.com")
        token = sig_request.token

    unsigned = client.get(f"/sign/{token}/document.pdf")
    assert unsigned.status_code == 200
    assert unsigned.data == b"%PDF-1.4 stub"

    client.post(f"/sign/{token}", json={
        "typed_name": "Dana Ortiz", "agreed": True, "signature_png_base64": _tiny_png_data_url(),
    })

    signed = client.get(f"/sign/{token}/document.pdf")
    assert signed.status_code == 200
    assert signed.data[:5] == b"%PDF-"
    assert signed.data != b"%PDF-1.4 stub"
