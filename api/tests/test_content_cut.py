import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "content"))

import cut  # noqa: E402

from extensions import db
from models import Asset, ContentItem


def _make_clip_item(batch_id="batch1", **meta_overrides):
    meta = {
        "batch_id": batch_id,
        "source_video": "/videos/source.mp4",
        "start": 10.0,
        "end": 35.0,
        "hook": "Most physicians never learn to read a room.",
        "payoff": "Here's the one question that fixes it.",
    }
    meta.update(meta_overrides)
    item = ContentItem(title="Clip 1", type="clip", status="draft", body="HOOK...", item_metadata=meta)
    db.session.add(item)
    db.session.commit()
    return item


def test_cut_clip_creates_two_assets_via_mocked_ffmpeg(app, tmp_path):
    with app.app_context():
        item = _make_clip_item()
        with patch("cut._run_ffmpeg") as mock_run:
            assets = cut.cut_clip(item, output_dir=str(tmp_path))

        assert mock_run.call_count == 2
        assert len(assets) == 2
        kinds = {a.kind for a in assets}
        assert kinds == {"video"}
        suffixes = sorted(os.path.basename(a.path).rsplit("_", 1)[-1] for a in assets)
        assert suffixes == ["1x1.mp4", "9x16.mp4"]

        db_assets = Asset.query.filter_by(content_item_id=item.id).all()
        assert len(db_assets) == 2


def test_cut_clip_rejects_non_clip_content_item(app):
    with app.app_context():
        item = ContentItem(title="Not a clip", type="linkedin_post", status="draft", body="x", item_metadata={})
        db.session.add(item)
        db.session.commit()

        try:
            cut.cut_clip(item)
            assert False, "expected CutError"
        except cut.CutError as e:
            assert "not a clip" in str(e)


def test_cut_clip_requires_source_and_timestamps(app):
    with app.app_context():
        item = ContentItem(title="Broken clip", type="clip", status="draft", body="x", item_metadata={"source_video": "x.mp4"})
        db.session.add(item)
        db.session.commit()

        try:
            cut.cut_clip(item)
            assert False, "expected CutError"
        except cut.CutError as e:
            assert "item_metadata" in str(e)


def test_cut_batch_cuts_every_clip_in_the_batch(app, tmp_path):
    with app.app_context():
        _make_clip_item(batch_id="batchA")
        _make_clip_item(batch_id="batchA")
        _make_clip_item(batch_id="batchB")  # different batch -- must not be touched

        with patch("cut._run_ffmpeg"):
            result = cut.cut_batch("batchA", output_dir=str(tmp_path))

        assert result["clips_cut"] == 2
        assert len(result["assets"]) == 2
        assert Asset.query.count() == 4  # 2 clips x 2 crops


def test_cut_batch_raises_for_unknown_batch(app):
    with app.app_context():
        try:
            cut.cut_batch("no-such-batch")
            assert False, "expected CutError"
        except cut.CutError as e:
            assert "no-such-batch" in str(e)


# ── real (unmocked) ffmpeg-missing gating — ffmpeg is not installed in this
#    environment, so this exercises the actual PATH-check -> clear-error
#    path with zero subprocess calls. ─────────────────────────────────────

def test_run_ffmpeg_raises_clear_error_when_binary_missing():
    import shutil
    if shutil.which("ffmpeg"):
        import pytest
        pytest.skip("ffmpeg is installed in this environment; gating path not exercised")

    try:
        cut._run_ffmpeg(["-version"])
        assert False, "expected CutError"
    except cut.CutError as e:
        assert "ffmpeg" in str(e).lower()
        assert "PATH" in str(e)
