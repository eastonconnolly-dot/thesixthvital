"""services/content_inventory.py -- inventory math, batch-ingest fault
tolerance, alert-once-per-drop behavior, and the defensive
ingest_consented_clips() integration point. All Claude/Whisper/ffmpeg
calls are mocked (batch_ingest never touches real content/ingest.py
internals here -- the whole module is replaced with a fake)."""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))  # repo root, for `ops.*`

from extensions import db
from models import ContentItem, InventoryAlertLog
from services import content_inventory


def _item(type_="linkedin_post", status="approved"):
    item = ContentItem(title="t", type=type_, status=status, body="b", item_metadata={})
    db.session.add(item)
    return item


# ── days_of_inventory_remaining ───────────────────────────────────────────

def test_zero_queued_linkedin_posts_is_zero_days(app):
    with app.app_context():
        assert content_inventory.days_of_inventory_remaining() == 0.0


def test_days_remaining_formula_approved_and_scheduled_count(app):
    with app.app_context():
        for _ in range(7):
            _item(status="approved")
        for _ in range(3):
            _item(status="scheduled")
        db.session.commit()
        # 10 queued posts / (5 per week / 7 days) = 14.0 days
        assert content_inventory.days_of_inventory_remaining() == 14.0


def test_days_remaining_excludes_other_statuses_and_types(app):
    with app.app_context():
        for _ in range(5):
            _item(status="approved")
        _item(status="draft")        # not yet cleared -- excluded
        _item(status="published")    # already out -- excluded
        _item(type_="newsletter", status="approved")  # not the cadence-bound type -- excluded
        _item(type_="clip", status="approved")
        db.session.commit()
        assert content_inventory.days_of_inventory_remaining() == 5 * 7 / 5


# ── batch_ingest ───────────────────────────────────────────────────────────

class _FakeIngestError(Exception):
    pass


def _fake_ingest_module(fail_on):
    fake = MagicMock()
    fake.IngestError = _FakeIngestError

    def _ingest(source):
        if source in fail_on:
            raise _FakeIngestError(f"bad video: {source}")
        return {"batch_id": "batch1", "source_video": source, "content_item_ids": [1, 2], "counts": {}}

    fake.ingest.side_effect = _ingest
    return fake


def test_batch_ingest_tolerates_one_failure_without_aborting_rest(app):
    fake_module = _fake_ingest_module(fail_on={"bad.mp4"})
    with app.app_context():
        with patch("services.content_inventory._content_ingest_module", return_value=fake_module):
            result = content_inventory.batch_ingest(["a.mp4", "bad.mp4", "c.mp4"])

    assert result["total"] == 3
    assert result["succeeded"] == 2
    assert result["failed"] == 1
    outcomes = {r["source"]: r for r in result["results"]}
    assert outcomes["a.mp4"]["ok"] is True
    assert outcomes["bad.mp4"]["ok"] is False
    assert "bad video" in outcomes["bad.mp4"]["error"]
    assert outcomes["c.mp4"]["ok"] is True


def test_batch_ingest_all_succeed(app):
    fake_module = _fake_ingest_module(fail_on=set())
    with app.app_context():
        with patch("services.content_inventory._content_ingest_module", return_value=fake_module):
            result = content_inventory.batch_ingest(["a.mp4", "b.mp4"])
    assert result["succeeded"] == 2
    assert result["failed"] == 0


# ── check_inventory_and_alert: once-per-drop behavior ──────────────────────

def _seed_days(app, days_target):
    """Seeds enough approved linkedin_post ContentItems to hit ~days_target
    days of inventory (count = days_target * 5 / 7, rounded)."""
    count = round(days_target * 5 / 7)
    for _ in range(count):
        _item(status="approved")
    db.session.commit()


def test_alert_fires_on_first_drop_below_threshold(app):
    with app.app_context():
        _seed_days(app, 10)  # well under 30
        with patch("ops.error_alerts.alert") as mock_alert:
            result = content_inventory.check_inventory_and_alert()

        assert result["alerted"] is True
        mock_alert.assert_called_once()
        assert InventoryAlertLog.query.count() == 1
        row = InventoryAlertLog.query.first()
        assert row.resolved_at is None


def test_alert_does_not_refire_while_still_below_threshold(app):
    with app.app_context():
        _seed_days(app, 10)
        with patch("ops.error_alerts.alert") as mock_alert:
            content_inventory.check_inventory_and_alert()
            result2 = content_inventory.check_inventory_and_alert()

        assert mock_alert.call_count == 1  # not called a second time
        assert result2["alerted"] is False
        assert InventoryAlertLog.query.count() == 1


def test_no_alert_when_inventory_healthy(app):
    with app.app_context():
        _seed_days(app, 60)  # well over 30
        with patch("ops.error_alerts.alert") as mock_alert:
            result = content_inventory.check_inventory_and_alert()

        mock_alert.assert_not_called()
        assert result["alerted"] is False
        assert InventoryAlertLog.query.count() == 0


def test_recovery_resolves_alert_and_next_drop_refires(app):
    with app.app_context():
        # Drop below threshold -> alert #1
        _seed_days(app, 10)
        with patch("ops.error_alerts.alert") as mock_alert:
            content_inventory.check_inventory_and_alert()
        assert mock_alert.call_count == 1
        active = InventoryAlertLog.query.filter_by(resolved_at=None).first()
        assert active is not None

        # Recover above threshold -> the active row gets resolved, no new alert
        for _ in range(50):
            _item(status="approved")
        db.session.commit()
        with patch("ops.error_alerts.alert") as mock_alert2:
            result = content_inventory.check_inventory_and_alert()
        mock_alert2.assert_not_called()
        assert result["alerted"] is False
        resolved = InventoryAlertLog.query.filter_by(resolved_at=None).first()
        assert resolved is None  # the earlier row was resolved

        # Drop below threshold again -> a fresh alert fires
        for item in ContentItem.query.all():
            db.session.delete(item)
        db.session.commit()
        _seed_days(app, 5)
        with patch("ops.error_alerts.alert") as mock_alert3:
            result3 = content_inventory.check_inventory_and_alert()
        mock_alert3.assert_called_once()
        assert result3["alerted"] is True
        assert InventoryAlertLog.query.count() == 2


# ── ingest_consented_clips: defensive integration point ────────────────────

def test_ingest_consented_clips_noop_when_model_absent(app, monkeypatch):
    import models
    monkeypatch.delattr(models, "ConsentRequest", raising=False)
    with app.app_context():
        result = content_inventory.ingest_consented_clips()
    assert result == {"available": False, "reason": "ConsentRequest model not defined", "ingested": None}


def test_ingest_consented_clips_noop_when_no_granted_rows(app):
    """ConsentRequest exists for real in this codebase today (built by
    another engineer's parallel Phase 6 work) but has no granted
    clip_consent rows -- should no-op cleanly, not crash."""
    from models import ConsentRequest

    with app.app_context():
        cr = ConsentRequest(kind="clip_consent", token="tok1", granted=False)
        db.session.add(cr)
        db.session.commit()

        result = content_inventory.ingest_consented_clips()

    assert result["available"] is True
    assert result["ingested"] == 0


def test_ingest_consented_clips_noop_when_no_path_field_yet(app):
    """ConsentRequest's real shape today has no clip/video path column at
    all -- granted rows exist but there's nothing to hand to batch_ingest()."""
    from models import ConsentRequest

    with app.app_context():
        cr = ConsentRequest(kind="clip_consent", token="tok2", granted=True)
        db.session.add(cr)
        db.session.commit()

        result = content_inventory.ingest_consented_clips()

    assert result["available"] is True
    assert result["ingested"] == 0
    assert "clip path field" in result["reason"]


def test_ingest_consented_clips_self_heals_once_path_field_present(app):
    """Simulates the future shape (a granted row that does carry a clip
    path) to prove the getattr chain picks it up and hands it to
    batch_ingest() the moment that field exists -- no code change needed."""
    from models import ConsentRequest

    fake_module = _fake_ingest_module(fail_on=set())
    with app.app_context():
        cr = ConsentRequest(kind="clip_consent", token="tok3", granted=True)
        db.session.add(cr)
        db.session.commit()
        cr.clip_path = "/videos/intensive_clip_1.mp4"  # not a mapped column -- simulates the future field

        with patch("services.content_inventory._content_ingest_module", return_value=fake_module):
            result = content_inventory.ingest_consented_clips()

    assert result["available"] is True
    assert result["ingested"] == 1
    assert result["summary"]["succeeded"] == 1
