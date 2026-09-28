from unittest.mock import patch

from extensions import db
from models import Deal, Lead, QualifierSession


def _make_lead(app, name="Dana Ortiz", email="dana@example.com", track="physician"):
    with app.app_context():
        lead = Lead(name=name, email=email, track=track, org="Cascade Orthopedics")
        db.session.add(lead)
        db.session.commit()
        return lead.id


def _complete_session(app, client, lead_id, budget_fit, recommended_package=None, brief="Solid fit."):
    with patch(
        "routes.qualifier.qualifier_chat.complete_qualifier",
        return_value={
            "brief": brief,
            "budget_fit_10k_plus": budget_fit,
            "recommended_package": recommended_package,
        },
    ):
        return client.post(f"/qualify/{lead_id}/complete")


# ── start ───────────────────────────────────────────────────────────────

def test_start_creates_session_and_returns_opening_question(app, client):
    lead_id = _make_lead(app)
    resp = client.post(f"/qualify/{lead_id}/start")
    assert resp.status_code == 201
    data = resp.get_json()
    assert "Dana" in data["question"]

    with app.app_context():
        session = db.session.get(QualifierSession, data["session_id"])
        assert session.status == "in_progress"
        assert session.transcript == [{"role": "assistant", "text": data["question"]}]


def test_start_is_idempotent_for_an_in_progress_session(app, client):
    lead_id = _make_lead(app)
    client.post(f"/qualify/{lead_id}/start")
    resp = client.post(f"/qualify/{lead_id}/start")
    assert resp.status_code == 200

    with app.app_context():
        assert QualifierSession.query.filter_by(lead_id=lead_id).count() == 1


# ── turn ────────────────────────────────────────────────────────────────

def test_turn_appends_lead_and_assistant_turns(app, client):
    lead_id = _make_lead(app)
    client.post(f"/qualify/{lead_id}/start")

    with patch("routes.qualifier.qualifier_chat.qualifier_reply", return_value="Got it — what's your timeline?") as mock_reply:
        resp = client.post(f"/qualify/{lead_id}/turn", json={"text": "We need this before residency interviews."})

    assert resp.status_code == 200
    assert resp.get_json()["reply"] == "Got it — what's your timeline?"
    mock_reply.assert_called_once()

    with app.app_context():
        session = QualifierSession.query.filter_by(lead_id=lead_id).first()
        assert [t["role"] for t in session.transcript] == ["assistant", "lead", "assistant"]


def test_turn_without_a_session_returns_404(app, client):
    lead_id = _make_lead(app)
    resp = client.post(f"/qualify/{lead_id}/turn", json={"text": "hello"})
    assert resp.status_code == 404


def test_turn_requires_nonempty_text(app, client):
    lead_id = _make_lead(app)
    client.post(f"/qualify/{lead_id}/start")
    resp = client.post(f"/qualify/{lead_id}/turn", json={"text": "  "})
    assert resp.status_code == 400


# ── complete: budget-fit branching ─────────────────────────────────────

def test_complete_budget_fit_opens_deal_at_discovery_with_brief(app, client):
    lead_id = _make_lead(app)
    client.post(f"/qualify/{lead_id}/start")

    resp = _complete_session(app, client, lead_id, budget_fit=True, brief="Wants coaching before Match.")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["budget_fit"] is True
    assert data["next"] == "book_a_call"
    deal_id = data["deal_id"]

    with app.app_context():
        deal = db.session.get(Deal, deal_id)
        assert deal.stage == "discovery"
        assert deal.qualifier_brief == "Wants coaching before Match."
        assert deal.lead_id == lead_id

        session = QualifierSession.query.filter_by(lead_id=lead_id).first()
        assert session.status == "completed"
        assert session.budget_fit is True
        assert session.recommended_package is None


def test_complete_reuses_existing_open_deal_instead_of_duplicating(app, client):
    lead_id = _make_lead(app)
    with app.app_context():
        existing = Deal(lead_id=lead_id, package="tbd", amount_cents=0, balance_due_cents=0, stage="discovery")
        db.session.add(existing)
        db.session.commit()
        existing_id = existing.id

    client.post(f"/qualify/{lead_id}/start")
    resp = _complete_session(app, client, lead_id, budget_fit=True)
    assert resp.get_json()["deal_id"] == existing_id

    with app.app_context():
        assert Deal.query.filter_by(lead_id=lead_id).count() == 1


def test_complete_not_fit_returns_recommended_package_and_checkout_url_no_deal(app, client):
    lead_id = _make_lead(app)
    client.post(f"/qualify/{lead_id}/start")

    resp = _complete_session(app, client, lead_id, budget_fit=False, recommended_package="rpsas_taste")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["budget_fit"] is False
    assert data["recommended_package"] == "rpsas_taste"
    assert data["checkout_url"]  # Stripe stub path (no STRIPE_SECRET_KEY in TestConfig)

    with app.app_context():
        assert Deal.query.filter_by(lead_id=lead_id).count() == 0
        session = QualifierSession.query.filter_by(lead_id=lead_id).first()
        assert session.budget_fit is False
        assert session.recommended_package == "rpsas_taste"


# ── the founder must never see a sub-$10k call ─────────────────────────

def test_sub_10k_lead_is_blocked_from_slots_and_booking(app, client):
    lead_id = _make_lead(app)
    client.post(f"/qualify/{lead_id}/start")
    _complete_session(app, client, lead_id, budget_fit=False, recommended_package="applicant_cohort_seat")

    slots_resp = client.get(f"/qualify/{lead_id}/slots")
    assert slots_resp.status_code == 403

    book_resp = client.post(f"/qualify/{lead_id}/book", json={"slot_start": "2026-10-05T14:00:00+00:00"})
    assert book_resp.status_code == 403

    with app.app_context():
        lead = db.session.get(Lead, lead_id)
        assert lead.status != "booked"


def test_lead_with_no_qualifier_session_is_blocked_from_booking(app, client):
    lead_id = _make_lead(app)
    assert client.get(f"/qualify/{lead_id}/slots").status_code == 403
    assert client.post(f"/qualify/{lead_id}/book", json={"slot_start": "2026-10-05T14:00:00+00:00"}).status_code == 403


# ── slots / book for a confirmed $10k+ fit ─────────────────────────────

def test_slots_returns_available_times_for_a_confirmed_fit(app, client):
    lead_id = _make_lead(app)
    client.post(f"/qualify/{lead_id}/start")
    _complete_session(app, client, lead_id, budget_fit=True)

    from datetime import datetime, timezone
    fake_slots = [datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc), datetime(2026, 10, 5, 14, 30, tzinfo=timezone.utc)]
    with patch("routes.qualifier.calendar_client.available_slots", return_value=fake_slots):
        resp = client.get(f"/qualify/{lead_id}/slots")

    assert resp.status_code == 200
    assert resp.get_json()["slots"] == [s.isoformat() for s in fake_slots]


def test_slots_returns_clean_502_when_calendar_not_configured(app, client):
    lead_id = _make_lead(app)
    client.post(f"/qualify/{lead_id}/start")
    _complete_session(app, client, lead_id, budget_fit=True)

    with patch("routes.qualifier.calendar_client.available_slots", side_effect=RuntimeError("GOOGLE_TOKEN_JSON not configured")):
        resp = client.get(f"/qualify/{lead_id}/slots")

    assert resp.status_code == 502
    assert "error" in resp.get_json()


def test_book_success_marks_lead_booked(app, client):
    lead_id = _make_lead(app)
    client.post(f"/qualify/{lead_id}/start")
    _complete_session(app, client, lead_id, budget_fit=True)

    with patch("routes.qualifier.calendar_client.book_slot", return_value={"id": "evt_123"}) as mock_book:
        resp = client.post(f"/qualify/{lead_id}/book", json={"slot_start": "2026-10-05T14:00:00+00:00"})

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["booked"] is True
    assert data["event_id"] == "evt_123"
    mock_book.assert_called_once()

    with app.app_context():
        lead = db.session.get(Lead, lead_id)
        assert lead.status == "booked"


def test_book_calendar_failure_returns_502_and_does_not_mark_booked(app, client):
    lead_id = _make_lead(app)
    client.post(f"/qualify/{lead_id}/start")
    _complete_session(app, client, lead_id, budget_fit=True)

    with patch("routes.qualifier.calendar_client.book_slot", side_effect=RuntimeError("no calendar creds")):
        resp = client.post(f"/qualify/{lead_id}/book", json={"slot_start": "2026-10-05T14:00:00+00:00"})

    assert resp.status_code == 502
    with app.app_context():
        lead = db.session.get(Lead, lead_id)
        assert lead.status != "booked"


def test_chat_page_renders(app, client):
    lead_id = _make_lead(app)
    resp = client.get(f"/qualify/{lead_id}")
    assert resp.status_code == 200
    assert f"const leadId = {lead_id};".encode() in resp.data  # the page's JS is scoped to this lead
