import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "content"))

import send_newsletter  # noqa: E402
import util as content_util  # noqa: E402

from extensions import db
from models import ContentItem, Lead, SuppressedEmail


def _make_newsletter(week, status="approved"):
    item = ContentItem(
        title="This week's letter", type="newsletter", status=status,
        body="Paragraph one.\n\nParagraph two.",
        item_metadata={"batch_id": "b1", "subject": "This week's letter", "send_week": week},
    )
    db.session.add(item)
    db.session.commit()
    return item


def _make_lead(email, status="nurture"):
    lead = Lead(name="Test Lead", email=email, track="physician", status=status)
    db.session.add(lead)
    db.session.commit()
    return lead


def test_find_current_newsletter_matches_current_week_and_ignores_drafts(app):
    with app.app_context():
        week = content_util.current_iso_week()
        draft = _make_newsletter(week, status="draft")
        approved = _make_newsletter(week + "-other", status="approved")  # wrong week
        matching = _make_newsletter(week, status="approved")

        found = send_newsletter.find_current_newsletter()
        assert found.id == matching.id


def test_send_newsletter_raises_when_nothing_approved_for_the_week(app):
    with app.app_context():
        try:
            send_newsletter.send_newsletter(week="2099-W01")
            assert False, "expected NewsletterSendError"
        except send_newsletter.NewsletterSendError as e:
            assert "2099-W01" in str(e)


def test_send_newsletter_sends_to_nurture_and_qualified_leads_only(app):
    with app.app_context():
        week = content_util.current_iso_week()
        item = _make_newsletter(week)

        nurture_lead = _make_lead("nurture@example.com", status="nurture")
        qualified_lead = _make_lead("qualified@example.com", status="qualified")
        _make_lead("new@example.com", status="new")  # not sendable
        _make_lead("customer@example.com", status="customer")  # not sendable

        with patch("send_newsletter.gmail_client.send_email", return_value={"message_id": "m1", "thread_id": "t1"}) as mock_send:
            result = send_newsletter.send_newsletter(item=item)

        assert mock_send.call_count == 2
        emails_sent = {r["email"] for r in result["recipients"]}
        assert emails_sent == {"nurture@example.com", "qualified@example.com"}

        assert item.status == "published"
        assert item.item_metadata["sent_count"] == 2


def test_send_newsletter_skips_suppressed_emails(app):
    with app.app_context():
        week = content_util.current_iso_week()
        item = _make_newsletter(week)

        _make_lead("ok@example.com", status="nurture")
        _make_lead("bounced@example.com", status="nurture")
        db.session.add(SuppressedEmail(email="bounced@example.com", reason="bounced"))
        db.session.commit()

        with patch("send_newsletter.gmail_client.send_email", return_value={"message_id": "m1", "thread_id": "t1"}) as mock_send:
            result = send_newsletter.send_newsletter(item=item)

        assert mock_send.call_count == 1
        assert result["recipients"][0]["email"] == "ok@example.com"


def test_send_newsletter_dry_run_sends_nothing_and_leaves_status_unchanged(app):
    with app.app_context():
        week = content_util.current_iso_week()
        item = _make_newsletter(week)
        _make_lead("ok@example.com", status="nurture")

        with patch("send_newsletter.gmail_client.send_email") as mock_send:
            result = send_newsletter.send_newsletter(item=item, dry_run=True)

        mock_send.assert_not_called()
        assert result["recipients"][0]["dry_run"] is True
        assert item.status == "approved"  # unchanged -- dry run never marks published
