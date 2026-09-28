from models import Application, Lead


def test_apply_creates_lead_and_qualified_application(app, client):
    resp = client.post("/apply", json={
        "name": "Priya Nair",
        "email": "priya@example.com",
        "track": "applicant",
        "org": "University Med School",
        "budget_ok": "yes",
        "situation": "Interview season starts in 6 weeks.",
    })
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["qualified"] is True

    with app.app_context():
        lead = Lead.query.get(data["lead_id"])
        assert lead.status == "qualified"
        application = Application.query.get(data["application_id"])
        assert application.qualified is True


def test_apply_routes_unqualified_to_nurture(app, client):
    resp = client.post("/apply", json={
        "name": "Sam Lee",
        "email": "sam@example.com",
        "track": "physician",
        "budget_ok": "unsure",
    })
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["qualified"] is False
    with app.app_context():
        assert Lead.query.get(data["lead_id"]).status == "nurture"


def test_apply_requires_name_email_track(client):
    resp = client.post("/apply", json={"email": "no-name@example.com", "track": "applicant"})
    assert resp.status_code == 400

    resp = client.post("/apply", json={"name": "No Track", "email": "x@example.com", "track": "not-real"})
    assert resp.status_code == 400
