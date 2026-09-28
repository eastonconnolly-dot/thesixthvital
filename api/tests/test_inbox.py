from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from extensions import db
from models import Lead, Message, MessageDraft, SequenceEnrollment, Sequence
from routes.inbox import bp as inbox_bp


@pytest.fixture
def client(app):
    """Registers the inbox blueprint (not yet wired into api/app.py -- see
    outreach/INTEGRATION.md) onto the shared `app` fixture so these routes
    are reachable through the test client, without touching app.py itself."""
    if "inbox" not in app.blueprints:
        app.register_blueprint(inbox_bp)
    return app.test_client()


def _login(client):
    return client.post("/admin/login", data={"password": "test-admin-password"}, follow_redirects=True)


def _make_reply(app, draft_kwargs=None, stop_reason=None):
    with app.app_context():
        lead = Lead(name="Dana Ortiz", email="dana@example.com", track="physician")
        db.session.add(lead)
        db.session.flush()

        if stop_reason:
            seq = Sequence(name="Nurture", track="physician")
            db.session.add(seq)
            db.session.flush()
            db.session.add(SequenceEnrollment(sequence_id=seq.id, lead_id=lead.id, status="stopped", stop_reason=stop_reason))

        message = Message(
            lead_id=lead.id, direction="inbound", channel="email",
            thread_id="thread-1", subject="Hello", body="Sure, let's talk!",
        )
        db.session.add(message)
        db.session.flush()

        if draft_kwargs is not None:
            db.session.add(MessageDraft(message_id=message.id, **draft_kwargs))

        db.session.commit()
        return message.id


# ── auth gating ──────────────────────────────────────────────────────────

def test_inbox_requires_login(client):
    resp = client.get("/admin/inbox")
    assert resp.status_code == 302


def test_inbox_send_requires_login(client):
    resp = client.post("/admin/inbox/1/send")
    assert resp.status_code == 302


def test_inbox_book_requires_login(client):
    resp = client.post("/admin/inbox/1/book")
    assert resp.status_code == 302


def test_inbox_dismiss_requires_login(client):
    resp = client.post("/admin/inbox/1/dismiss")
    assert resp.status_code == 302


# ── listing ──────────────────────────────────────────────────────────────

def test_inbox_lists_replied_messages(app, client):
    message_id = _make_reply(app, draft_kwargs={"positive": True, "confidence": 0.8, "draft_body": "Sounds great!"})
    with app.app_context():
        m = db.session.get(Message, message_id)
        m.replied = True
        db.session.commit()

    _login(client)
    resp = client.get("/admin/inbox")
    assert resp.status_code == 200
    assert b"Dana Ortiz" in resp.data
    assert b"Sounds great!" in resp.data


def test_inbox_lists_messages_tied_to_stopped_for_reply_enrollment(app, client):
    _make_reply(app, draft_kwargs=None, stop_reason="replied")
    _login(client)
    resp = client.get("/admin/inbox")
    assert resp.status_code == 200
    assert b"Dana Ortiz" in resp.data


def test_inbox_hides_dismissed_messages(app, client):
    message_id = _make_reply(app, draft_kwargs={"positive": False, "confidence": 0.1, "dismissed": True})
    with app.app_context():
        db.session.get(Message, message_id).replied = True
        db.session.commit()

    _login(client)
    resp = client.get("/admin/inbox")
    assert b"Dana Ortiz" not in resp.data


# ── send ─────────────────────────────────────────────────────────────────

def test_send_reply_sends_drafted_body_and_marks_approved(app, client):
    message_id = _make_reply(app, draft_kwargs={"positive": True, "confidence": 0.9, "draft_body": "Happy to chat!"})
    _login(client)

    with patch("routes.inbox.gmail_client.send_email", return_value={"message_id": "m2", "thread_id": "thread-1"}) as mock_send:
        resp = client.post(f"/admin/inbox/{message_id}/send", follow_redirects=True)
    assert resp.status_code == 200
    mock_send.assert_called_once()
    assert mock_send.call_args.kwargs["thread_id"] == "thread-1"

    with app.app_context():
        message = db.session.get(Message, message_id)
        assert message.draft.approved is True
        outbound = Message.query.filter_by(direction="outbound").first()
        assert outbound is not None
        assert outbound.body == "Happy to chat!"


def test_send_reply_without_draft_flashes_error(app, client):
    message_id = _make_reply(app, draft_kwargs=None)
    _login(client)
    with patch("routes.inbox.gmail_client.send_email") as mock_send:
        resp = client.post(f"/admin/inbox/{message_id}/send", follow_redirects=True)
    assert resp.status_code == 200
    mock_send.assert_not_called()


# ── book ─────────────────────────────────────────────────────────────────

def test_book_call_books_first_proposed_slot(app, client):
    slot = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc).isoformat()
    message_id = _make_reply(app, draft_kwargs={"positive": True, "confidence": 0.9, "proposed_slots": [slot]})
    _login(client)

    with patch("routes.inbox.calendar_client.book_slot") as mock_book:
        resp = client.post(f"/admin/inbox/{message_id}/book", follow_redirects=True)
    assert resp.status_code == 200
    mock_book.assert_called_once()

    with app.app_context():
        message = db.session.get(Message, message_id)
        assert message.lead.status == "booked"
        assert message.draft.approved is True


def test_book_call_without_slots_flashes_error(app, client):
    message_id = _make_reply(app, draft_kwargs={"positive": True, "confidence": 0.9, "proposed_slots": []})
    _login(client)
    with patch("routes.inbox.calendar_client.book_slot") as mock_book:
        resp = client.post(f"/admin/inbox/{message_id}/book", follow_redirects=True)
    assert resp.status_code == 200
    mock_book.assert_not_called()


# ── dismiss ──────────────────────────────────────────────────────────────

def test_dismiss_marks_draft_dismissed(app, client):
    message_id = _make_reply(app, draft_kwargs={"positive": False, "confidence": 0.2})
    _login(client)
    resp = client.post(f"/admin/inbox/{message_id}/dismiss", follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        assert db.session.get(Message, message_id).draft.dismissed is True


def test_dismiss_creates_draft_when_none_exists(app, client):
    message_id = _make_reply(app, draft_kwargs=None)
    _login(client)
    resp = client.post(f"/admin/inbox/{message_id}/dismiss", follow_redirects=True)
    assert resp.status_code == 200
    with app.app_context():
        draft = db.session.get(Message, message_id).draft
        assert draft is not None
        assert draft.dismissed is True
