from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from extensions import db
from models import Lead, MagicLinkToken, PracticeSession, PracticeUser
from services import magic_link


def _login_via_magic_link(app, client, email="dana@example.com", track="physician"):
    with app.app_context():
        url, user = magic_link.request_login(email, track=track, name="Dana Ortiz")
        token = url.rsplit("/", 1)[-1]
        user_id = user.id
    resp = client.get(f"/practice/auth/{token}")
    assert resp.status_code == 302
    return user_id


# ── magic link ──────────────────────────────────────────────────────────

def test_request_login_creates_user_and_lead(app):
    with app.app_context():
        url, user = magic_link.request_login("new@example.com", track="applicant", name="New Person")
        assert user.track == "applicant"
        assert user.subscription_status == "trialing"
        assert user.trial_ends_at is not None
        lead = db.session.get(Lead, user.lead_id)
        assert lead.email == "new@example.com"
        assert lead.source == "practice_app"


def test_request_login_requires_track_for_new_email(app):
    with app.app_context():
        try:
            magic_link.request_login("brandnew@example.com")
            assert False, "expected ValueError"
        except ValueError:
            pass


def test_request_login_reuses_existing_user(app):
    with app.app_context():
        _, user1 = magic_link.request_login("dup@example.com", track="physician")
        _, user2 = magic_link.request_login("dup@example.com")
        assert user1.id == user2.id


def test_magic_link_token_single_use(app):
    with app.app_context():
        url, _ = magic_link.request_login("once@example.com", track="physician")
        token = url.rsplit("/", 1)[-1]
        first = magic_link.verify_and_consume(token)
        second = magic_link.verify_and_consume(token)
        assert first is not None
        assert second is None


def test_magic_link_token_expired(app):
    with app.app_context():
        url, _ = magic_link.request_login("expired@example.com", track="physician")
        token_str = url.rsplit("/", 1)[-1]
        row = MagicLinkToken.query.filter_by(token=token_str).first()
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.session.commit()
        assert magic_link.verify_and_consume(token_str) is None


def test_auth_endpoint_rejects_unknown_token(client):
    resp = client.get("/practice/auth/not-a-real-token")
    assert resp.status_code == 400


# ── session flow ────────────────────────────────────────────────────────

def test_dashboard_requires_login(client):
    resp = client.get("/practice")
    assert resp.status_code == 302


def test_create_session_calls_patient_sim_and_persists(app, client):
    _login_via_magic_link(app, client)
    with patch("routes.practice.patient_sim.start_encounter", return_value="Have a seat.") as mock_start, \
         patch("routes.practice.patient_sim.random_shift_turn", return_value=4):
        resp = client.post("/practice/sessions", json={"scenario_key": "bad_news", "mode": "proof"})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["opening_line"] == "Have a seat."
    mock_start.assert_called_once_with("bad_news", "proof")

    with app.app_context():
        s = db.session.get(PracticeSession, data["session_id"])
        assert s.shift_after_turn == 4
        assert s.transcript == [{"role": "patient", "text": "Have a seat."}]


def test_create_session_returns_503_when_anthropic_not_configured(app, client):
    _login_via_magic_link(app, client)
    app.config["ANTHROPIC_API_KEY"] = ""
    try:
        resp = client.post("/practice/sessions", json={"scenario_key": "bad_news"})
        assert resp.status_code == 503
    finally:
        app.config["ANTHROPIC_API_KEY"] = "sk-ant-test-stub"


def test_create_session_rejects_unknown_scenario(app, client):
    _login_via_magic_link(app, client)
    resp = client.post("/practice/sessions", json={"scenario_key": "not_real"})
    assert resp.status_code == 400


def test_create_session_blocked_when_trial_expired_and_not_subscribed(app, client):
    user_id = _login_via_magic_link(app, client, email="expired-trial@example.com")
    with app.app_context():
        user = db.session.get(PracticeUser, user_id)
        user.trial_ends_at = datetime.now(timezone.utc) - timedelta(days=1)
        user.subscription_status = None
        db.session.commit()

    resp = client.post("/practice/sessions", json={"scenario_key": "bad_news"})
    assert resp.status_code == 402


def test_turn_triggers_shift_at_configured_turn_and_only_once(app, client):
    _login_via_magic_link(app, client)
    with patch("routes.practice.patient_sim.start_encounter", return_value="opening"), \
         patch("routes.practice.patient_sim.random_shift_turn", return_value=2):
        resp = client.post("/practice/sessions", json={"scenario_key": "bad_news", "mode": "proof"})
    session_id = resp.get_json()["session_id"]

    with patch("routes.practice.patient_sim.patient_reply", return_value="reply 1") as mock_reply, \
         patch("routes.practice.patient_sim.random_mode", return_value="permission"):
        r1 = client.post(f"/practice/sessions/{session_id}/turn", json={"text": "first thing I say"})
        assert r1.get_json()["shift_occurred"] is False
        call_kwargs = mock_reply.call_args.kwargs
        assert call_kwargs["is_shift_turn"] is False

        r2 = client.post(f"/practice/sessions/{session_id}/turn", json={"text": "second thing I say"})
        assert r2.get_json()["shift_occurred"] is True
        call_kwargs = mock_reply.call_args.kwargs
        assert call_kwargs["is_shift_turn"] is True
        assert call_kwargs["shift_to_mode"] == "permission"

    with app.app_context():
        s = db.session.get(PracticeSession, session_id)
        assert s.shift_to_mode == "permission"

    with patch("routes.practice.patient_sim.patient_reply", return_value="reply 3") as mock_reply3:
        client.post(f"/practice/sessions/{session_id}/turn", json={"text": "third thing"})
        # shift already happened -- must not re-trigger even though turn count keeps climbing
        assert mock_reply3.call_args.kwargs["is_shift_turn"] is False


def test_turn_rejects_empty_text(app, client):
    _login_via_magic_link(app, client)
    with patch("routes.practice.patient_sim.start_encounter", return_value="opening"), \
         patch("routes.practice.patient_sim.random_shift_turn", return_value=4):
        resp = client.post("/practice/sessions", json={"scenario_key": "bad_news"})
    session_id = resp.get_json()["session_id"]
    resp = client.post(f"/practice/sessions/{session_id}/turn", json={"text": "  "})
    assert resp.status_code == 400


def test_complete_session_persists_scores(app, client):
    _login_via_magic_link(app, client)
    with patch("routes.practice.patient_sim.start_encounter", return_value="opening"), \
         patch("routes.practice.patient_sim.random_shift_turn", return_value=4):
        resp = client.post("/practice/sessions", json={"scenario_key": "bad_news"})
    session_id = resp.get_json()["session_id"]

    fake_result = {
        "scores": {"read_accuracy": 4, "mode_match": 3, "delivery": 5, "adaptation": 4, "outcome": 4},
        "quotes": {"read_accuracy": "q1", "mode_match": "q2", "delivery": "q3", "adaptation": "q4", "outcome": "q5"},
        "named_p_initial": "proof", "named_p_after_shift": None,
        "shift_caught": False, "drill": "Practice sitting with silence.",
    }
    with patch("routes.practice.patient_sim.score_encounter", return_value=fake_result):
        resp = client.post(f"/practice/sessions/{session_id}/complete")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] == 20
    assert data["drill"] == "Practice sitting with silence."

    with app.app_context():
        s = db.session.get(PracticeSession, session_id)
        assert s.status == "completed"
        assert s.completed_at is not None


def test_lesson_completion_is_idempotent(app, client):
    _login_via_magic_link(app, client)
    r1 = client.post("/practice/lessons/read/complete")
    r2 = client.post("/practice/lessons/read/complete")
    assert r1.status_code == 200 and r2.status_code == 200
    with app.app_context():
        from models import MicroLessonProgress
        assert MicroLessonProgress.query.count() == 1


def test_lesson_completion_rejects_unknown_key(app, client):
    _login_via_magic_link(app, client)
    resp = client.post("/practice/lessons/not_a_lesson/complete")
    assert resp.status_code == 404


def test_lesson_detail_renders_first_unlocked_lesson(app, client):
    _login_via_magic_link(app, client)
    resp = client.get("/practice/lessons/read")
    assert resp.status_code == 200
    assert b"Room, Emotion, Angle, Desire" in resp.data
    assert b"Mark complete" in resp.data


def test_lesson_detail_blocks_locked_lesson(app, client):
    _login_via_magic_link(app, client)
    # "shift" is the last lesson -- nothing has been completed yet, so it's locked
    resp = client.get("/practice/lessons/shift")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/practice")


def test_lesson_detail_allows_a_completed_lesson_again(app, client):
    _login_via_magic_link(app, client)
    client.post("/practice/lessons/read/complete")
    resp = client.get("/practice/lessons/read")
    assert resp.status_code == 200
    assert b"Completed" in resp.data
    assert b"Mark complete" not in resp.data


def test_lesson_detail_unlocks_next_lesson_only_after_completing_prior(app, client):
    _login_via_magic_link(app, client)
    # "pick" is locked before "read" is completed
    assert client.get("/practice/lessons/pick").status_code == 302
    client.post("/practice/lessons/read/complete")
    assert client.get("/practice/lessons/pick").status_code == 200


def test_lesson_detail_404_for_unknown_key(app, client):
    _login_via_magic_link(app, client)
    resp = client.get("/practice/lessons/not_a_lesson")
    assert resp.status_code == 404


# ── subscription checkout + webhook ────────────────────────────────────

def test_subscribe_returns_stub_checkout_when_stripe_unconfigured(app, client):
    _login_via_magic_link(app, client)
    resp = client.post("/practice/subscribe")
    assert resp.status_code == 200
    assert "checkout_url" in resp.get_json()


def test_subscription_webhook_updates_status(app, client):
    user_id = _login_via_magic_link(app, client, email="sub@example.com")
    with app.app_context():
        user = db.session.get(PracticeUser, user_id)
        user.stripe_subscription_id = "sub_123"
        db.session.commit()

    fake_event = {
        "type": "customer.subscription.updated",
        "data": {"object": {"id": "sub_123", "status": "active"}},
    }
    with patch("routes.webhooks.stripe_client.verify_and_parse_webhook", return_value=fake_event):
        resp = client.post("/webhooks/stripe", data=b"{}", headers={"Stripe-Signature": "t=1,v1=fake"})
    assert resp.status_code == 200

    with app.app_context():
        assert db.session.get(PracticeUser, user_id).subscription_status == "active"
