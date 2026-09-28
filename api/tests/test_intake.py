from extensions import db
from models import CohortRoster, Deal, IntakeForm, Lead


def _make_deal(app, track="physician"):
    lead = Lead(name="Dana Ortiz", email="dana@example.com", track=track)
    db.session.add(lead)
    db.session.flush()
    deal = Deal(lead_id=lead.id, package="physician_private", amount_cents=1250000, balance_due_cents=1250000)
    db.session.add(deal)
    db.session.commit()
    return deal


# ── intake form ────────────────────────────────────────────────────────────

def test_intake_form_renders_for_valid_token(app, client):
    with app.app_context():
        deal = _make_deal(app)
        db.session.add(IntakeForm(deal_id=deal.id, token="intake-tok-1"))
        db.session.commit()

    resp = client.get("/intake/intake-tok-1")
    assert resp.status_code == 200


def test_intake_form_404_for_unknown_token(client):
    assert client.get("/intake/not-a-real-token").status_code == 404
    assert client.post("/intake/not-a-real-token", data={}).status_code == 404


def test_intake_submit_persists_responses(app, client):
    with app.app_context():
        deal = _make_deal(app)
        db.session.add(IntakeForm(deal_id=deal.id, token="intake-tok-2"))
        db.session.commit()

    resp = client.post("/intake/intake-tok-2", data={
        "emergency_contact": "Sam Ortiz, 555-1234", "sizing": "L",
        "accessibility": "", "special_requests": "Window seat",
    })
    assert resp.status_code == 200
    assert b"received" in resp.data.lower()

    with app.app_context():
        form = IntakeForm.query.filter_by(token="intake-tok-2").first()
        assert form.submitted_at is not None
        assert form.responses["sizing"] == "L"
        assert form.responses["special_requests"] == "Window seat"


def test_intake_submit_rejects_second_submission(app, client):
    with app.app_context():
        deal = _make_deal(app)
        db.session.add(IntakeForm(deal_id=deal.id, token="intake-tok-3"))
        db.session.commit()

    first = client.post("/intake/intake-tok-3", data={"emergency_contact": "a"})
    assert first.status_code == 200
    second = client.post("/intake/intake-tok-3", data={"emergency_contact": "b"})
    assert second.status_code == 409


# ── cohort roster form ──────────────────────────────────────────────────────

def test_roster_form_renders_for_valid_token(app, client):
    with app.app_context():
        deal = _make_deal(app, track="program")
        db.session.add(CohortRoster(deal_id=deal.id, token="roster-tok-1"))
        db.session.commit()

    resp = client.get("/roster/roster-tok-1")
    assert resp.status_code == 200


def test_roster_form_404_for_unknown_token(client):
    assert client.get("/roster/not-a-real-token").status_code == 404
    assert client.post("/roster/not-a-real-token", data={}).status_code == 404


def test_roster_submit_persists_participants_and_checklist(app, client):
    with app.app_context():
        deal = _make_deal(app, track="program")
        db.session.add(CohortRoster(deal_id=deal.id, token="roster-tok-2"))
        db.session.commit()

    resp = client.post("/roster/roster-tok-2", data={
        "participant_name": ["Alex Kim", "Jordan Lee", ""],
        "participant_email": ["alex@example.com", "jordan@example.com", ""],
        "projector": "on", "whiteboard": "on",
    })
    assert resp.status_code == 200
    assert b"received" in resp.data.lower()

    with app.app_context():
        roster = CohortRoster.query.filter_by(token="roster-tok-2").first()
        assert roster.submitted_at is not None
        assert len(roster.participants) == 2  # the blank trailing row is dropped
        assert roster.participants[0] == {"name": "Alex Kim", "email": "alex@example.com"}
        assert roster.av_checklist["projector"] is True
        assert roster.av_checklist["whiteboard"] is True
        assert roster.av_checklist["breakout_space"] is False


def test_roster_submit_rejects_second_submission(app, client):
    with app.app_context():
        deal = _make_deal(app, track="program")
        db.session.add(CohortRoster(deal_id=deal.id, token="roster-tok-3"))
        db.session.commit()

    first = client.post("/roster/roster-tok-3", data={"participant_name": ["A"], "participant_email": ["a@example.com"]})
    assert first.status_code == 200
    second = client.post("/roster/roster-tok-3", data={"participant_name": ["B"], "participant_email": ["b@example.com"]})
    assert second.status_code == 409
