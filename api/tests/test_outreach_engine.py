import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))  # repo root, for outreach.*

from extensions import db
from models import Lead, Message, Sequence, SequenceEnrollment, SequenceStep, SuppressedEmail
from models import _aware
from outreach.engine import classifier, reply_detection, sequences


def _make_sequence(app, track="physician", delays=(0, 4, 4)):
    with app.app_context():
        seq = Sequence(name=f"Nurture — {track}", track=track)
        db.session.add(seq)
        db.session.flush()
        for i, delay in enumerate(delays):
            db.session.add(SequenceStep(
                sequence_id=seq.id, step_order=i, delay_days=delay, channel="email",
                subject=f"Subject {i} for {{{{first_name}}}}", body=f"Body {i} for {{{{name}}}} at {{{{org}}}}",
            ))
        db.session.commit()
        return seq.id


def _make_lead(app, **kwargs):
    with app.app_context():
        defaults = dict(name="Dana Ortiz", email="dana@example.com", track="physician")
        defaults.update(kwargs)
        lead = Lead(**defaults)
        db.session.add(lead)
        db.session.commit()
        return lead.id


# ── enroll_lead ──────────────────────────────────────────────────────────

def test_enroll_lead_sets_next_fire_at_from_first_step_delay(app):
    seq_id = _make_sequence(app, delays=(0, 4, 4))
    lead_id = _make_lead(app)
    with app.app_context():
        lead, seq = db.session.get(Lead, lead_id), db.session.get(Sequence, seq_id)
        before = datetime.now(timezone.utc)
        enrollment = sequences.enroll_lead(lead, seq)
        assert enrollment.status == "active"
        assert enrollment.current_step_index == 0
        assert abs((_aware(enrollment.next_fire_at) - before).total_seconds()) < 5  # delay_days=0 -> fires ~now


# ── tick(): basic send + advance ────────────────────────────────────────

def test_tick_sends_due_email_and_advances_to_next_step(app):
    seq_id = _make_sequence(app, delays=(0, 4, 4))
    lead_id = _make_lead(app, name="Dana Ortiz", org="Cascade Clinic")

    with app.app_context():
        lead, seq = db.session.get(Lead, lead_id), db.session.get(Sequence, seq_id)
        enrollment = sequences.enroll_lead(lead, seq)
        enrollment.next_fire_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.session.commit()
        enrollment_id = enrollment.id

    with app.app_context():
        with patch("outreach.engine.sequences.gmail_client.send_email",
                   return_value={"message_id": "m1", "thread_id": "t1"}) as mock_send:
            summary = sequences.tick()

    assert summary["sent_email"] == 1
    mock_send.assert_called_once()
    _, kwargs = mock_send.call_args
    assert kwargs["to_email"] == "dana@example.com"
    assert "Dana" in kwargs["subject"]  # {{first_name}} rendered

    with app.app_context():
        enrollment = db.session.get(SequenceEnrollment, enrollment_id)
        assert enrollment.current_step_index == 1
        assert enrollment.status == "active"
        messages = Message.query.filter_by(lead_id=lead_id).all()
        assert len(messages) == 1
        assert messages[0].direction == "outbound"


def test_tick_completes_enrollment_after_last_step(app):
    seq_id = _make_sequence(app, delays=(0,))  # single-step sequence
    lead_id = _make_lead(app)

    with app.app_context():
        lead, seq = db.session.get(Lead, lead_id), db.session.get(Sequence, seq_id)
        enrollment = sequences.enroll_lead(lead, seq)
        enrollment.next_fire_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.session.commit()
        enrollment_id = enrollment.id

    with app.app_context():
        with patch("outreach.engine.sequences.gmail_client.send_email",
                   return_value={"message_id": "m1", "thread_id": "t1"}):
            sequences.tick()

    with app.app_context():
        enrollment = db.session.get(SequenceEnrollment, enrollment_id)
        assert enrollment.status == "completed"
        assert enrollment.next_fire_at is None


def test_tick_stops_enrollment_when_lead_email_suppressed(app):
    seq_id = _make_sequence(app)
    lead_id = _make_lead(app, email="blocked@example.com")

    with app.app_context():
        db.session.add(SuppressedEmail(email="blocked@example.com", reason="bounced"))
        lead, seq = db.session.get(Lead, lead_id), db.session.get(Sequence, seq_id)
        enrollment = sequences.enroll_lead(lead, seq)
        enrollment.next_fire_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.session.commit()
        enrollment_id = enrollment.id

    with app.app_context():
        with patch("outreach.engine.sequences.gmail_client.send_email") as mock_send:
            summary = sequences.tick()

    assert summary["skipped_suppressed"] == 1
    mock_send.assert_not_called()
    with app.app_context():
        enrollment = db.session.get(SequenceEnrollment, enrollment_id)
        assert enrollment.status == "stopped"
        assert enrollment.stop_reason == "suppressed"


def test_tick_respects_daily_send_cap(app):
    seq_id = _make_sequence(app)
    lead1_id = _make_lead(app, email="a@example.com")
    lead2_id = _make_lead(app, email="b@example.com")

    with app.app_context():
        app.config["SEQUENCE_DAILY_SEND_CAP"] = 1
        app.config["SEQUENCE_RAMP_START_CAP"] = 1  # no history yet -> ramp defaults to start cap anyway
        seq = db.session.get(Sequence, seq_id)
        for lead_id in (lead1_id, lead2_id):
            lead = db.session.get(Lead, lead_id)
            enrollment = sequences.enroll_lead(lead, seq)
            enrollment.next_fire_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.session.commit()

    with app.app_context():
        with patch("outreach.engine.sequences.gmail_client.send_email",
                   return_value={"message_id": "m1", "thread_id": "t1"}):
            summary = sequences.tick()

    assert summary["sent_email"] == 1
    assert summary["skipped_capped"] == 1


def test_daily_cap_ramps_linearly_then_caps(app):
    with app.app_context():
        app.config["SEQUENCE_DAILY_SEND_CAP"] = 80
        app.config["SEQUENCE_RAMP_START_CAP"] = 10
        app.config["SEQUENCE_RAMP_DAYS"] = 14

        # No sending history at all yet -> start of ramp.
        assert sequences._daily_cap(datetime.now(timezone.utc)) == 10

        first_sent = datetime.now(timezone.utc) - timedelta(days=7)
        db.session.add(Message(direction="outbound", channel="email", sent_at=first_sent))
        db.session.commit()
        mid_ramp_cap = sequences._daily_cap(datetime.now(timezone.utc))
        assert 10 < mid_ramp_cap < 80

        db.session.add(Message(
            direction="outbound", channel="email",
            sent_at=datetime.now(timezone.utc) - timedelta(days=20),
        ))
        db.session.commit()
        assert sequences._daily_cap(datetime.now(timezone.utc)) == 80  # fully ramped


# ── tick(): SMS gating ───────────────────────────────────────────────────

def _make_sms_sequence(app):
    with app.app_context():
        seq = Sequence(name="SMS test", track="physician")
        db.session.add(seq)
        db.session.flush()
        db.session.add(SequenceStep(sequence_id=seq.id, step_order=0, delay_days=0, channel="sms", body="Hi {{first_name}}"))
        db.session.commit()
        return seq.id


def test_tick_skips_sms_step_when_quo_key_unset(app):
    seq_id = _make_sms_sequence(app)
    lead_id = _make_lead(app, phone="509-555-0100")

    with app.app_context():
        app.config["QUO_API_KEY"] = ""
        lead, seq = db.session.get(Lead, lead_id), db.session.get(Sequence, seq_id)
        enrollment = sequences.enroll_lead(lead, seq)
        enrollment.next_fire_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.session.commit()
        enrollment_id = enrollment.id

    with app.app_context():
        with patch("outreach.engine.sequences.requests.post") as mock_post:
            summary = sequences.tick()

    assert summary["sms_skipped"] == 1
    assert summary["sent_sms"] == 0
    mock_post.assert_not_called()
    with app.app_context():
        assert db.session.get(SequenceEnrollment, enrollment_id).status == "completed"


def test_tick_skips_sms_step_when_lead_not_eligible(app):
    """Physician track + phone are present, but there's no prior outbound
    email engagement -- SMS must still be skipped even with a key configured."""
    seq_id = _make_sms_sequence(app)
    lead_id = _make_lead(app, phone="509-555-0100")

    with app.app_context():
        app.config["QUO_API_KEY"] = "test-quo-key"
        lead, seq = db.session.get(Lead, lead_id), db.session.get(Sequence, seq_id)
        enrollment = sequences.enroll_lead(lead, seq)
        enrollment.next_fire_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.session.commit()

    with app.app_context():
        with patch("outreach.engine.sequences.requests.post") as mock_post:
            summary = sequences.tick()

    assert summary["sms_skipped"] == 1
    mock_post.assert_not_called()


def test_tick_sends_sms_when_configured_and_lead_eligible(app):
    seq_id = _make_sms_sequence(app)
    lead_id = _make_lead(app, phone="509-555-0100")

    with app.app_context():
        app.config["QUO_API_KEY"] = "test-quo-key"
        db.session.add(Message(lead_id=lead_id, direction="outbound", channel="email"))  # prior engagement
        lead, seq = db.session.get(Lead, lead_id), db.session.get(Sequence, seq_id)
        enrollment = sequences.enroll_lead(lead, seq)
        enrollment.next_fire_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.session.commit()

    with app.app_context():
        with patch("outreach.engine.sequences.requests.post", return_value=Mock(raise_for_status=lambda: None)) as mock_post:
            summary = sequences.tick()

    assert summary["sent_sms"] == 1
    mock_post.assert_called_once()


# ── reply_detection ──────────────────────────────────────────────────────

def test_check_for_replies_marks_replied_creates_inbound_and_stops_enrollment(app):
    seq_id = _make_sequence(app)
    lead_id = _make_lead(app)

    with app.app_context():
        lead, seq = db.session.get(Lead, lead_id), db.session.get(Sequence, seq_id)
        enrollment = sequences.enroll_lead(lead, seq)
        outbound = Message(
            lead_id=lead_id, direction="outbound", channel="email",
            thread_id="thread-1", subject="Hello", replied=False,
        )
        db.session.add(outbound)
        db.session.commit()
        enrollment_id, outbound_id = enrollment.id, outbound.id

    thread_payload = [{"id": "m1"}, {"id": "m2", "snippet": "Sure, I'd love to chat!"}]
    with app.app_context():
        with patch("outreach.engine.reply_detection.gmail_client.list_thread_replies", return_value=thread_payload):
            new_ids = reply_detection.check_for_replies(classify=False)

    assert len(new_ids) == 1
    with app.app_context():
        outbound = db.session.get(Message, outbound_id)
        assert outbound.replied is True
        inbound = db.session.get(Message, new_ids[0])
        assert inbound.direction == "inbound"
        assert inbound.body == "Sure, I'd love to chat!"
        enrollment = db.session.get(SequenceEnrollment, enrollment_id)
        assert enrollment.status == "stopped"
        assert enrollment.stop_reason == "replied"


def test_check_for_replies_leaves_unreplied_threads_untouched(app):
    lead_id = _make_lead(app)
    with app.app_context():
        outbound = Message(lead_id=lead_id, direction="outbound", channel="email", thread_id="thread-2", replied=False)
        db.session.add(outbound)
        db.session.commit()
        outbound_id = outbound.id

    with app.app_context():
        with patch("outreach.engine.reply_detection.gmail_client.list_thread_replies", return_value=[{"id": "m1"}]):
            new_ids = reply_detection.check_for_replies(classify=False)

    assert new_ids == []
    with app.app_context():
        assert db.session.get(Message, outbound_id).replied is False


def test_check_for_replies_skips_thread_on_gmail_failure(app):
    lead_id = _make_lead(app)
    with app.app_context():
        db.session.add(Message(lead_id=lead_id, direction="outbound", channel="email", thread_id="thread-3", replied=False))
        db.session.commit()

    with app.app_context():
        with patch("outreach.engine.reply_detection.gmail_client.list_thread_replies", side_effect=Exception("gmail down")):
            new_ids = reply_detection.check_for_replies(classify=False)
    assert new_ids == []


# ── classifier ───────────────────────────────────────────────────────────

def _fake_claude_response(payload):
    resp = Mock()
    block = Mock(type="text", text=json.dumps(payload))
    resp.content = [block]
    return resp


def test_classify_reply_returns_none_when_anthropic_not_configured(app):
    lead_id = _make_lead(app)
    with app.app_context():
        message = Message(lead_id=lead_id, direction="inbound", channel="email", body="not interested")
        db.session.add(message)
        db.session.commit()
        message_id = message.id
        app.config["ANTHROPIC_API_KEY"] = ""
        result = classifier.classify_reply(message_id)
    assert result is None


def test_classify_reply_positive_creates_draft_with_proposed_slots(app):
    lead_id = _make_lead(app)
    with app.app_context():
        message = Message(lead_id=lead_id, direction="inbound", channel="email", body="Yes, let's set up a call!")
        db.session.add(message)
        db.session.commit()
        message_id = message.id

        app.config["ANTHROPIC_API_KEY"] = "sk-ant-test"
        payload = {"positive": True, "confidence": 0.92, "reasoning": "asked for a call",
                   "draft_reply": "Happy to set that up -- here are a few times that work."}
        fake_slots = [datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc), datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)]

        with patch("outreach.engine.classifier.claude_client.client") as mock_client, \
             patch("outreach.engine.classifier.calendar_client.available_slots", return_value=fake_slots):
            mock_client.return_value.messages.create.return_value = _fake_claude_response(payload)
            draft = classifier.classify_reply(message_id)

        assert draft.positive is True
        assert draft.confidence == 0.92
        assert "Happy to set that up" in draft.draft_body
        assert len(draft.proposed_slots) == 2


def test_classify_reply_negative_creates_draft_without_slots(app):
    lead_id = _make_lead(app)
    with app.app_context():
        message = Message(lead_id=lead_id, direction="inbound", channel="email", body="Please remove me from this list.")
        db.session.add(message)
        db.session.commit()
        message_id = message.id

        app.config["ANTHROPIC_API_KEY"] = "sk-ant-test"
        payload = {"positive": False, "confidence": 0.98, "reasoning": "unsubscribe request", "draft_reply": None}

        with patch("outreach.engine.classifier.claude_client.client") as mock_client, \
             patch("outreach.engine.classifier.calendar_client.available_slots") as mock_slots:
            mock_client.return_value.messages.create.return_value = _fake_claude_response(payload)
            draft = classifier.classify_reply(message_id)

        assert draft.positive is False
        assert draft.proposed_slots == []
        mock_slots.assert_not_called()
