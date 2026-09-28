import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "content"))

import ingest  # noqa: E402

from extensions import db
from models import ContentItem

FAKE_SEGMENTS = [
    {"start": 0.0, "end": 4.0, "text": "Most physicians never learn how to read a room."},
    {"start": 4.0, "end": 9.5, "text": "That single skill is what separates a trusted doctor from a rushed one."},
]

FAKE_PLAN = {
    "clips": [
        {"title": f"Clip {i}", "start": float(i * 10), "end": float(i * 10 + 30), "hook": "h", "payoff": "p"}
        for i in range(5)
    ],
    "linkedin_posts": [
        {"weekday": wd, "body": f"Post for {wd} about reading the room."}
        for wd in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    ],
    "newsletter": {"subject": "This week: reading the room", "body": "Body paragraph one.\n\nBody paragraph two."},
    "captions": ["Caption one", "Caption two", "Caption three"],
    "proof_snippets": ["Snippet one", "Snippet two"],
}


def _counts_by_type():
    counts = {}
    for item in ContentItem.query.all():
        counts[item.type] = counts.get(item.type, 0) + 1
    return counts


def test_ingest_local_file_creates_expected_content_items(app, tmp_path):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"not a real video, just needs to exist")

    with app.app_context():
        with patch("ingest._transcribe", return_value=FAKE_SEGMENTS) as mock_transcribe, \
             patch("ingest._plan_content", return_value=FAKE_PLAN) as mock_plan:
            result = ingest.ingest(str(video_path))

        mock_transcribe.assert_called_once()
        mock_plan.assert_called_once()

        assert result["source_video"] == str(video_path)
        assert len(result["content_item_ids"]) == 5 + 5 + 1 + 3 + 2

        counts = _counts_by_type()
        assert counts["clip"] == 5
        assert counts["linkedin_post"] == 5
        assert counts["newsletter"] == 1
        assert counts["caption"] == 3
        assert counts["proof_snippet"] == 2

        for item in ContentItem.query.all():
            assert item.status == "draft"
            assert (item.item_metadata or {}).get("batch_id") == result["batch_id"]
            assert (item.item_metadata or {}).get("source_video") == str(video_path)


def test_ingest_appends_apply_cta_to_every_linkedin_post(app, tmp_path):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"fake")

    plan = dict(FAKE_PLAN)
    plan["linkedin_posts"] = [
        {"weekday": "Monday", "body": "A post with no CTA at all."},
    ] + FAKE_PLAN["linkedin_posts"][1:]

    with app.app_context():
        with patch("ingest._transcribe", return_value=FAKE_SEGMENTS), \
             patch("ingest._plan_content", return_value=plan):
            ingest.ingest(str(video_path))

        apply_url = f"{app.config['SITE_BASE_URL']}/apply.html"
        posts = ContentItem.query.filter_by(type="linkedin_post").all()
        assert len(posts) == 5
        for post in posts:
            assert apply_url in post.body


def test_ingest_clamps_clips_longer_than_60_seconds(app, tmp_path):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"fake")

    plan = dict(FAKE_PLAN)
    plan["clips"] = [
        {"title": "Too long", "start": 0.0, "end": 200.0, "hook": "h", "payoff": "p"}
    ] + FAKE_PLAN["clips"][1:]

    with app.app_context():
        with patch("ingest._transcribe", return_value=FAKE_SEGMENTS), \
             patch("ingest._plan_content", return_value=plan):
            ingest.ingest(str(video_path))

        clip = ContentItem.query.filter_by(type="clip", title="Too long").first()
        meta = clip.item_metadata
        assert meta["end"] - meta["start"] == 60


def test_ingest_raises_clear_error_for_missing_local_file(app):
    with app.app_context():
        try:
            ingest.ingest("/no/such/video.mp4")
            assert False, "expected IngestError"
        except ingest.IngestError as e:
            assert "not found" in str(e)


def test_ingest_youtube_url_downloads_then_proceeds(app):
    with app.app_context():
        with patch("ingest._download_youtube", return_value="/tmp/downloaded.mp4") as mock_dl, \
             patch("ingest._transcribe", return_value=FAKE_SEGMENTS), \
             patch("ingest._plan_content", return_value=FAKE_PLAN):
            result = ingest.ingest("https://www.youtube.com/watch?v=abc123")

        mock_dl.assert_called_once()
        assert mock_dl.call_args[0][0] == "https://www.youtube.com/watch?v=abc123"
        assert result["source_video"] == "https://www.youtube.com/watch?v=abc123"


def test_ingest_wraps_youtube_download_failures(app):
    def _boom(url, dest_dir):
        raise ingest.IngestError(f"yt-dlp failed to download {url}: boom")

    with app.app_context():
        with patch("ingest._download_youtube", side_effect=_boom):
            try:
                ingest.ingest("https://youtu.be/xyz")
                assert False, "expected IngestError"
            except ingest.IngestError as e:
                assert "yt-dlp failed" in str(e)


# ── real (unmocked) gating behavior — neither yt-dlp nor a local Whisper
#    package is installed in this environment, so these exercise the actual
#    ImportError -> clear-error path with zero network/model calls. ────────

def test_download_youtube_raises_clear_error_without_ytdlp(tmp_path):
    try:
        import yt_dlp  # noqa: F401
        import pytest
        pytest.skip("yt-dlp is installed in this environment; gating path not exercised")
    except ImportError:
        pass

    try:
        ingest._download_youtube("https://youtu.be/abc", str(tmp_path))
        assert False, "expected IngestError"
    except ingest.IngestError as e:
        assert "yt-dlp" in str(e)


def test_transcribe_raises_clear_error_without_local_whisper():
    try:
        import faster_whisper  # noqa: F401
        has_faster = True
    except ImportError:
        has_faster = False
    try:
        import whisper  # noqa: F401
        has_openai = True
    except ImportError:
        has_openai = False

    if has_faster or has_openai:
        import pytest
        pytest.skip("a local Whisper package is installed; gating path not exercised")

    try:
        ingest._transcribe("/some/video.mp4")
        assert False, "expected IngestError"
    except ingest.IngestError as e:
        assert "Whisper" in str(e) or "whisper" in str(e)


# ── _clamp_plan_counts (the real enforcement now that Claude's structured-
# output schema can't express minItems/maxItems other than 0/1) ──────────

def test_clamp_plan_counts_truncates_oversized_lists():
    plan = {
        "clips": [{"title": f"c{i}"} for i in range(10)],
        "linkedin_posts": [{"weekday": "Monday", "body": f"p{i}"} for i in range(8)],
        "captions": [f"cap{i}" for i in range(6)],
        "proof_snippets": [f"snip{i}" for i in range(5)],
    }
    clamped = ingest._clamp_plan_counts(plan)
    assert len(clamped["clips"]) == 7
    assert len(clamped["linkedin_posts"]) == 5
    assert len(clamped["captions"]) == 3
    assert len(clamped["proof_snippets"]) == 2


def test_clamp_plan_counts_leaves_undersized_lists_alone():
    plan = {
        "clips": [{"title": "only one"}],
        "linkedin_posts": [],
        "captions": ["one caption"],
        "proof_snippets": [],
    }
    clamped = ingest._clamp_plan_counts(plan)
    assert len(clamped["clips"]) == 1
    assert len(clamped["linkedin_posts"]) == 0
    assert len(clamped["captions"]) == 1
    assert len(clamped["proof_snippets"]) == 0
