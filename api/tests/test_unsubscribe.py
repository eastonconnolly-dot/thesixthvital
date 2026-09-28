import pytest

from extensions import db
from models import Lead, Sequence, SequenceEnrollment, SuppressedEmail
from routes.unsubscribe import bp as unsubscribe_bp


@pytest.fixture
def client(app):
    """Registers the standalone unsubscribe blueprint (not yet wired into
    api/app.py -- see outreach/INTEGRATION.md) so it's reachable through the
    test client without touching app.py itself."""
    if "unsubscribe" not in app.blueprints:
        app.register_blueprint(unsubscribe_bp)
    return app.test_client()


def _make_lead_with_active_enrollment(app):
    with app.app_context():
        lead = Lead(name="Pat Lee", email="pat@example.com", track="physician")
        db.session.add(lead)
        db.session.flush()
        seq = Sequence(name="Nurture", track="physician")
        db.session.add(seq)
        db.session.flush()
        enrollment = SequenceEnrollment(sequence_id=seq.id, lead_id=lead.id, status="active")
        db.session.add(enrollment)
        db.session.commit()
        return lead.id, enrollment.id


def test_unsubscribe_suppresses_email_and_stops_enrollments(app, client):
    lead_id, enrollment_id = _make_lead_with_active_enrollment(app)
    resp = client.get(f"/unsubscribe?lead_id={lead_id}")
    assert resp.status_code == 200
    assert b"unsubscribed" in resp.data.lower()

    with app.app_context():
        assert SuppressedEmail.query.filter_by(email="pat@example.com").count() == 1
        enrollment = db.session.get(SequenceEnrollment, enrollment_id)
        assert enrollment.status == "stopped"
        assert enrollment.stop_reason == "unsubscribed"
        assert db.session.get(Lead, lead_id).status == "unsubscribed"


def test_unsubscribe_is_idempotent(app, client):
    lead_id, _ = _make_lead_with_active_enrollment(app)
    client.get(f"/unsubscribe?lead_id={lead_id}")
    resp = client.get(f"/unsubscribe?lead_id={lead_id}")
    assert resp.status_code == 200
    with app.app_context():
        assert SuppressedEmail.query.filter_by(email="pat@example.com").count() == 1


def test_unsubscribe_rejects_missing_lead_id(client):
    resp = client.get("/unsubscribe")
    assert resp.status_code == 400


def test_unsubscribe_rejects_unknown_lead_id(client):
    resp = client.get("/unsubscribe?lead_id=999999")
    assert resp.status_code == 400
