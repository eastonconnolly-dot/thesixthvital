import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "content"))

import schedule  # noqa: E402

from extensions import db
from models import ContentItem


def _make_item(status="approved", type_="linkedin_post"):
    item = ContentItem(title="A post", type=type_, status=status, body="ready to go", item_metadata={})
    db.session.add(item)
    db.session.commit()
    return item


def test_schedule_item_marks_scheduled_locally_without_any_key_configured(app):
    with app.app_context():
        assert not app.config.get("BUFFER_API_KEY")
        assert not app.config.get("PUBLER_API_KEY")

        item = _make_item()
        with patch("schedule.logger") as mock_logger:
            result = schedule.schedule_item(item)
            mock_logger.warning.assert_called_once()

        assert result["pushed_to"] is None
        assert item.status == "scheduled"
        assert item.item_metadata["scheduled_via"] is None


def test_schedule_item_pushes_to_buffer_when_key_configured(app):
    with app.app_context():
        app.config["BUFFER_API_KEY"] = "fake-buffer-key"
        try:
            item = _make_item()
            with patch("schedule._push_to_buffer", return_value={"id": "buf_123"}) as mock_push:
                result = schedule.schedule_item(item)
            mock_push.assert_called_once()
            assert result["pushed_to"] == "buffer"
            assert result["external_id"] == "buf_123"
            assert item.status == "scheduled"
        finally:
            app.config["BUFFER_API_KEY"] = ""


def test_schedule_item_prefers_buffer_over_publer_when_both_configured(app):
    with app.app_context():
        app.config["BUFFER_API_KEY"] = "buf-key"
        app.config["PUBLER_API_KEY"] = "pub-key"
        try:
            item = _make_item()
            with patch("schedule._push_to_buffer", return_value={"id": "buf_1"}) as mock_buffer, \
                 patch("schedule._push_to_publer") as mock_publer:
                schedule.schedule_item(item)
            mock_buffer.assert_called_once()
            mock_publer.assert_not_called()
        finally:
            app.config["BUFFER_API_KEY"] = ""
            app.config["PUBLER_API_KEY"] = ""


def test_schedule_item_refuses_non_approved_items(app):
    with app.app_context():
        item = _make_item(status="draft")
        try:
            schedule.schedule_item(item)
            assert False, "expected ScheduleError"
        except schedule.ScheduleError as e:
            assert "not approved" in str(e)
        assert item.status == "draft"


def test_schedule_all_approved_only_touches_approved_items(app):
    with app.app_context():
        approved = _make_item(status="approved")
        draft = _make_item(status="draft")
        already_scheduled = _make_item(status="scheduled")

        results = schedule.schedule_all_approved()

        assert len(results) == 1
        assert results[0]["content_item_id"] == approved.id
        assert draft.status == "draft"
        assert already_scheduled.status == "scheduled"
