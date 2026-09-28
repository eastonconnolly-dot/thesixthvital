from datetime import date, timedelta
from unittest.mock import patch

from extensions import db
from models import CohortRoster, Deal, IntakeForm, Lead, UploadLink
from services import onboarding


def _make_deal(app, track="physician", delivery_date=None, package="physician_private"):
    lead = Lead(name="Dana Ortiz", email="dana@example.com", track=track)
    db.session.add(lead)
    db.session.flush()
    deal = Deal(
        lead_id=lead.id, package=package, amount_cents=1250000,
        balance_due_cents=1250000, stage="deposit_paid", delivery_date=delivery_date,
    )
    db.session.add(deal)
    db.session.commit()
    return deal


# ── trigger_onboarding ────────────────────────────────────────────────────

def test_trigger_onboarding_creates_intake_and_upload_link(app):
    with app.app_context():
        deal = _make_deal(app)
        with patch("services.onboarding.gmail_client.send_email", return_value={"message_id": "m1", "thread_id": "t1"}) as mock_send, \
             patch("services.onboarding.calendar_client.book_slot") as mock_book, \
             patch("services.onboarding.submit_badge_print_order") as mock_badge:
            result = onboarding.trigger_onboarding(deal)

        assert result["status"] == "ok"
        assert result["welcome_email_sent"] is True
        assert result["intake_form_created"] is True
        assert result["upload_links_created"] == 1
        assert mock_send.call_count == 1  # just the welcome email -- not program-track, no roster email
        mock_badge.assert_called_once()

        deal = db.session.get(Deal, deal.id)
        assert deal.onboarding_triggered_at is not None
        assert IntakeForm.query.filter_by(deal_id=deal.id).count() == 1
        assert UploadLink.query.filter_by(deal_id=deal.id).count() == 1


def test_trigger_onboarding_is_idempotent(app):
    with app.app_context():
        deal = _make_deal(app)
        with patch("services.onboarding.gmail_client.send_email", return_value={"message_id": "m1", "thread_id": "t1"}) as mock_send, \
             patch("services.onboarding.calendar_client.book_slot"), \
             patch("services.onboarding.submit_badge_print_order"):
            first = onboarding.trigger_onboarding(deal)
            second = onboarding.trigger_onboarding(deal)

        assert first["status"] == "ok"
        assert second == {"status": "already_triggered"}
        assert mock_send.call_count == 1  # not re-sent on the second call
        assert IntakeForm.query.filter_by(deal_id=deal.id).count() == 1
        assert UploadLink.query.filter_by(deal_id=deal.id).count() == 1


def test_trigger_onboarding_books_calendar_holds_when_delivery_date_set(app):
    with app.app_context():
        deal = _make_deal(app, delivery_date=date(2026, 11, 15))
        with patch("services.onboarding.gmail_client.send_email", return_value={"message_id": "m1", "thread_id": "t1"}), \
             patch("services.onboarding.calendar_client.book_slot") as mock_book, \
             patch("services.onboarding.submit_badge_print_order"):
            result = onboarding.trigger_onboarding(deal)

        assert result["calendar_holds"] == ["delivery", "checkin_30day"]
        assert mock_book.call_count == 2


def test_trigger_onboarding_skips_calendar_holds_when_no_delivery_date(app):
    with app.app_context():
        deal = _make_deal(app, delivery_date=None)
        with patch("services.onboarding.gmail_client.send_email", return_value={"message_id": "m1", "thread_id": "t1"}), \
             patch("services.onboarding.calendar_client.book_slot") as mock_book, \
             patch("services.onboarding.submit_badge_print_order"):
            result = onboarding.trigger_onboarding(deal)

        assert result["calendar_holds"] == []
        mock_book.assert_not_called()


def test_trigger_onboarding_survives_calendar_failure(app):
    """A Calendar API hiccup must not block the rest of onboarding."""
    with app.app_context():
        deal = _make_deal(app, delivery_date=date(2026, 11, 15))
        with patch("services.onboarding.gmail_client.send_email", return_value={"message_id": "m1", "thread_id": "t1"}) as mock_send, \
             patch("services.onboarding.calendar_client.book_slot", side_effect=RuntimeError("calendar down")), \
             patch("services.onboarding.submit_badge_print_order"):
            result = onboarding.trigger_onboarding(deal)

        assert result["status"] == "ok"
        assert result["calendar_holds"] == []
        assert result["welcome_email_sent"] is True
        deal = db.session.get(Deal, deal.id)
        assert deal.onboarding_triggered_at is not None


def test_ensure_calendar_holds_books_retroactively_once_delivery_date_is_set(app):
    """The bug this guards against: trigger_onboarding() is one-shot, so a
    deal onboarded before its delivery_date was known (a call-to-proposal
    extraction with no date in the transcript, or a self-serve package
    checkout with no discovery call at all) would otherwise skip calendar
    holds forever -- there's no second trigger_onboarding() call to retry
    them. ensure_calendar_holds() is the retry path (wired into
    routes/admin.py's set_delivery_date)."""
    with app.app_context():
        deal = _make_deal(app, delivery_date=None)
        with patch("services.onboarding.gmail_client.send_email", return_value={"message_id": "m1", "thread_id": "t1"}), \
             patch("services.onboarding.calendar_client.book_slot") as mock_book, \
             patch("services.onboarding.submit_badge_print_order"):
            result = onboarding.trigger_onboarding(deal)

        assert result["calendar_holds"] == []
        mock_book.assert_not_called()
        deal = db.session.get(Deal, deal.id)
        assert deal.calendar_holds_booked_at is None

        deal.delivery_date = date(2026, 11, 15)
        db.session.commit()

        with patch("services.onboarding.calendar_client.book_slot") as mock_book2:
            booked = onboarding.ensure_calendar_holds(deal)

        assert booked == ["delivery", "checkin_30day"]
        assert mock_book2.call_count == 2
        deal = db.session.get(Deal, deal.id)
        assert deal.calendar_holds_booked_at is not None


def test_ensure_calendar_holds_is_idempotent(app):
    with app.app_context():
        deal = _make_deal(app, delivery_date=date(2026, 11, 15))
        with patch("services.onboarding.gmail_client.send_email", return_value={"message_id": "m1", "thread_id": "t1"}), \
             patch("services.onboarding.calendar_client.book_slot") as mock_book, \
             patch("services.onboarding.submit_badge_print_order"):
            onboarding.trigger_onboarding(deal)

        assert mock_book.call_count == 2

        with patch("services.onboarding.calendar_client.book_slot") as mock_book2:
            again = onboarding.ensure_calendar_holds(deal)

        assert again == []
        mock_book2.assert_not_called()


def test_trigger_onboarding_program_track_creates_cohort_roster(app):
    with app.app_context():
        deal = _make_deal(app, track="program", package="program_cohort_1day")
        with patch("services.onboarding.gmail_client.send_email", return_value={"message_id": "m1", "thread_id": "t1"}) as mock_send, \
             patch("services.onboarding.calendar_client.book_slot"), \
             patch("services.onboarding.submit_badge_print_order"):
            result = onboarding.trigger_onboarding(deal)

        assert result["cohort_roster_created"] is True
        assert CohortRoster.query.filter_by(deal_id=deal.id).count() == 1
        # welcome email + roster-ask email
        assert mock_send.call_count == 2


def test_trigger_onboarding_non_program_track_skips_roster(app):
    with app.app_context():
        deal = _make_deal(app, track="physician")
        with patch("services.onboarding.gmail_client.send_email", return_value={"message_id": "m1", "thread_id": "t1"}), \
             patch("services.onboarding.calendar_client.book_slot"), \
             patch("services.onboarding.submit_badge_print_order"):
            onboarding.trigger_onboarding(deal)

        assert CohortRoster.query.filter_by(deal_id=deal.id).count() == 0


# ── send_delivery_reminders ────────────────────────────────────────────────

def test_send_delivery_reminders_sends_at_each_threshold(app):
    with app.app_context():
        today = date(2026, 9, 27)
        deal7 = _make_deal(app, delivery_date=today + timedelta(days=7))
        deal3 = _make_deal(app, delivery_date=today + timedelta(days=3))
        deal1 = _make_deal(app, delivery_date=today + timedelta(days=1))
        deal_far = _make_deal(app, delivery_date=today + timedelta(days=20))

        from datetime import datetime, timezone
        now = datetime(2026, 9, 27, tzinfo=timezone.utc)

        with patch("services.onboarding.gmail_client.send_email", return_value={"message_id": "m", "thread_id": "t"}) as mock_send:
            result = onboarding.send_delivery_reminders(now=now)

        assert result["sent"] == 3
        assert mock_send.call_count == 3

        assert db.session.get(Deal, deal7.id).reminder_7d_sent is True
        assert db.session.get(Deal, deal3.id).reminder_3d_sent is True
        assert db.session.get(Deal, deal1.id).reminder_1d_sent is True
        assert db.session.get(Deal, deal_far.id).reminder_7d_sent is False


def test_send_delivery_reminders_does_not_resend(app):
    with app.app_context():
        from datetime import datetime, timezone
        today = date(2026, 9, 27)
        deal = _make_deal(app, delivery_date=today + timedelta(days=7))
        now = datetime(2026, 9, 27, tzinfo=timezone.utc)

        with patch("services.onboarding.gmail_client.send_email", return_value={"message_id": "m", "thread_id": "t"}) as mock_send:
            onboarding.send_delivery_reminders(now=now)
            result = onboarding.send_delivery_reminders(now=now)

        assert result["sent"] == 0
        assert result["skipped"] == 1
        assert mock_send.call_count == 1


def test_send_delivery_reminders_skips_deals_without_delivery_date(app):
    with app.app_context():
        from datetime import datetime, timezone
        _make_deal(app, delivery_date=None)
        with patch("services.onboarding.gmail_client.send_email") as mock_send:
            result = onboarding.send_delivery_reminders(now=datetime(2026, 9, 27, tzinfo=timezone.utc))
        assert result["sent"] == 0
        mock_send.assert_not_called()
