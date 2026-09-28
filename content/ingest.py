"""content/ingest.py — turns one source video into a batch of draft
ContentItems: clips, LinkedIn posts, a newsletter, captions, and outreach
proof snippets.

    source video (local file path OR a YouTube URL)
        -> local transcription with timestamps (faster-whisper / openai-whisper —
           NOT the paid OpenAI Whisper API; see _transcribe())
        -> one Claude call that reads the transcript and proposes:
             - 5-7 clip windows (hook + payoff, each <= 60s)
             - 5 LinkedIn posts (one per weekday), each ending with the
               application CTA
             - 1 weekly newsletter (Thursday send)
             - 3 short-form captions
             - 2 outreach-ready proof snippets
        -> every item persisted as a ContentItem row, status="draft"

Nothing here ever sets status to anything other than "draft" — the approval
gate lives entirely in api/routes/content_admin.py. A YouTube URL is
downloaded via yt-dlp; if yt-dlp isn't installed, or the download fails,
ingest() raises a clear IngestError instead of a raw traceback — this
project deliberately doesn't scrape or pull video from anywhere the founder
didn't explicitly hand it a URL or file for.

Usage:
    cd content && python ingest.py /path/to/video.mp4
    cd content && python ingest.py "https://www.youtube.com/watch?v=..."
"""

import argparse
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # so `import util` resolves however this is imported

import util  # noqa: E402

util.add_api_to_path()

from flask import current_app  # noqa: E402

from extensions import db  # noqa: E402
from models import ContentItem  # noqa: E402
from services import claude_client  # noqa: E402

MAX_CLIP_SECONDS = 60
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
PLAN_MAX_TOKENS = 4000


class IngestError(Exception):
    """Raised for anything that should stop ingestion with a clear message
    rather than a raw traceback: missing file, failed download, missing
    local Whisper package, or a malformed Claude response."""


# ── source acquisition ──────────────────────────────────────────────────

def _looks_like_url(source):
    return source.startswith("http://") or source.startswith("https://")


def _download_youtube(url, dest_dir):
    """Downloads url (YouTube or anything else yt-dlp supports) into
    dest_dir via yt-dlp, returning the local file path. This is the only
    place this package reaches outside the file the founder pointed it at —
    no crawling, no "related videos", just the one URL given."""
    try:
        import yt_dlp
    except ImportError:
        raise IngestError(
            "yt-dlp is not installed. Run `pip install yt-dlp` to ingest from a "
            "URL, or pass a local video file path instead."
        )

    os.makedirs(dest_dir, exist_ok=True)
    ydl_opts = {
        "outtmpl": os.path.join(dest_dir, "%(id)s.%(ext)s"),
        "format": "mp4/bestvideo+bestaudio/best",
        "quiet": True,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            return ydl.prepare_filename(info)
    except IngestError:
        raise
    except Exception as e:  # yt_dlp raises its own broad DownloadError etc.
        raise IngestError(f"yt-dlp failed to download {url}: {e}")


# ── transcription (local / self-hosted only) ────────────────────────────

def _transcribe(video_path, model_size="base"):
    """Local, self-hosted transcription with segment timestamps. Tries
    faster-whisper first (lighter footprint), then openai-whisper. Neither
    is the paid OpenAI API — both run entirely on this machine. Raises
    IngestError with install instructions if neither package is present."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        pass
    else:
        model = WhisperModel(model_size)
        segments, _info = model.transcribe(video_path)
        return [{"start": float(s.start), "end": float(s.end), "text": s.text.strip()} for s in segments]

    try:
        import whisper
    except ImportError:
        raise IngestError(
            "No local Whisper package is installed. Run `pip install faster-whisper` "
            "or `pip install openai-whisper` — both are self-hosted, no OpenAI API key "
            "needed, no per-minute transcription bill."
        )

    model = whisper.load_model(model_size)
    result = model.transcribe(video_path)
    return [
        {"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip()}
        for s in result["segments"]
    ]


def _format_transcript(segments):
    lines = []
    for seg in segments:
        mm, ss = divmod(int(seg["start"]), 60)
        lines.append(f"[{mm:02d}:{ss:02d}] {seg['text'].strip()}")
    return "\n".join(lines)


# ── Claude: transcript -> content plan ──────────────────────────────────


# Claude's structured-output schema validator only supports array minItems/
# maxItems values of 0 or 1 (discovered live: "For 'array' type, 'minItems'
# values other than 0 or 1 are not supported") -- same family of restriction
# as services/patient_sim.py's integer minimum/maximum and null-in-enum
# limits. The exact counts below are requested in the prompt text instead
# and enforced in Python after parsing (see _clamp_plan_counts()) rather
# than relying on the schema to guarantee them.
CONTENT_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "hook": {"type": "string"},
                    "payoff": {"type": "string"},
                },
                "required": ["title", "start", "end", "hook", "payoff"],
                "additionalProperties": False,
            },
        },
        "linkedin_posts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "weekday": {"type": "string", "enum": WEEKDAYS},
                    "body": {"type": "string"},
                },
                "required": ["weekday", "body"],
                "additionalProperties": False,
            },
        },
        "newsletter": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["subject", "body"],
            "additionalProperties": False,
        },
        "captions": {"type": "array", "items": {"type": "string"}},
        "proof_snippets": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["clips", "linkedin_posts", "newsletter", "captions", "proof_snippets"],
    "additionalProperties": False,
}


def _plan_content(transcript_text, apply_url):
    """One Claude call, JSON-schema-constrained, turning a timestamped
    transcript into the full content plan. Mirrors the output_config
    pattern already used by services/patient_sim.py's score_encounter()."""
    prompt = (
        "You are the content strategist for Sixth Vital, a physician-communication "
        "training company. Below is a timestamped transcript of a source video "
        "(a webinar, coaching session, or talk). From it, produce a content plan:\n\n"
        "- 5 to 7 clip windows, each a self-contained hook + payoff moment, each "
        "spanning at most 60 seconds. start/end are seconds from the top of the "
        "video, matched to the transcript timestamps.\n"
        "- Exactly 5 LinkedIn posts, one for each weekday Monday through Friday, in "
        "a direct, credible, no-hype voice. Each should end by inviting the reader "
        f"to apply at {apply_url}.\n"
        "- Exactly 1 weekly newsletter (subject + body) for a Thursday send.\n"
        "- Exactly 3 short-form captions for vertical/short clips (Reels/Shorts-style).\n"
        "- Exactly 2 outreach-ready proof snippets: short, quotable lines a rep can "
        "paste into a cold email or LinkedIn DM as social proof.\n\n"
        f"TRANSCRIPT:\n{transcript_text}"
    )
    resp = claude_client.client().messages.create(
        model=claude_client.model(),
        max_tokens=PLAN_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": CONTENT_PLAN_SCHEMA}},
    )
    text = next(b.text for b in resp.content if b.type == "text")
    return _clamp_plan_counts(json.loads(text))


def _clamp_plan_counts(plan):
    """The real enforcement of clips=5-7 / posts=5 / captions=3 / proof_snippets=2
    now that the schema itself can't guarantee it (see CONTENT_PLAN_SCHEMA's
    comment). Truncates any list that came back too long; a too-short list is
    left as-is (better to publish 4 good LinkedIn posts than fabricate a 5th)."""
    plan["clips"] = plan.get("clips", [])[:7]
    plan["linkedin_posts"] = plan.get("linkedin_posts", [])[:5]
    plan["captions"] = plan.get("captions", [])[:3]
    plan["proof_snippets"] = plan.get("proof_snippets", [])[:2]
    return plan


# ── persistence ──────────────────────────────────────────────────────────

def _apply_url():
    return f"{current_app.config['SITE_BASE_URL']}/apply.html"


def _ensure_cta(body, apply_url):
    body = (body or "").rstrip()
    if apply_url in body:
        return body
    return f"{body}\n\nReady to build this into how your team communicates? Apply: {apply_url}"


def _persist_plan(plan, batch_id, source_label):
    items = []

    for clip in plan["clips"]:
        start, end = float(clip["start"]), float(clip["end"])
        if end < start:
            start, end = end, start
        if end - start > MAX_CLIP_SECONDS:
            end = start + MAX_CLIP_SECONDS
        items.append(ContentItem(
            title=clip.get("title") or "Untitled clip",
            type="clip",
            status="draft",
            body=f"HOOK: {clip.get('hook', '')}\n\nPAYOFF: {clip.get('payoff', '')}",
            item_metadata={
                "batch_id": batch_id,
                "source_video": source_label,
                "start": start,
                "end": end,
                "hook": clip.get("hook", ""),
                "payoff": clip.get("payoff", ""),
            },
        ))

    apply_url = _apply_url()
    for post in plan["linkedin_posts"]:
        items.append(ContentItem(
            title=f"LinkedIn — {post['weekday']}",
            type="linkedin_post",
            status="draft",
            body=_ensure_cta(post["body"], apply_url),
            item_metadata={"batch_id": batch_id, "source_video": source_label, "weekday": post["weekday"]},
        ))

    newsletter = plan["newsletter"]
    items.append(ContentItem(
        title=newsletter.get("subject") or "Sixth Vital Weekly",
        type="newsletter",
        status="draft",
        body=newsletter.get("body", ""),
        item_metadata={
            "batch_id": batch_id,
            "source_video": source_label,
            "subject": newsletter.get("subject"),
            "send_week": util.next_thursday_iso_week(),
        },
    ))

    for i, caption in enumerate(plan["captions"], start=1):
        items.append(ContentItem(
            title=f"Caption {i}",
            type="caption",
            status="draft",
            body=caption,
            item_metadata={"batch_id": batch_id, "source_video": source_label},
        ))

    for i, snippet in enumerate(plan["proof_snippets"], start=1):
        items.append(ContentItem(
            title=f"Proof snippet {i}",
            type="proof_snippet",
            status="draft",
            body=snippet,
            item_metadata={"batch_id": batch_id, "source_video": source_label},
        ))

    db.session.add_all(items)
    db.session.commit()
    return items


def _counts(items):
    counts = {}
    for item in items:
        counts[item.type] = counts.get(item.type, 0) + 1
    return counts


# ── entrypoint ───────────────────────────────────────────────────────────

def ingest(source, batch_id=None):
    """Runs the full pipeline for one source video/URL. Must be called
    inside a Flask app_context(). Returns
    {"batch_id", "source_video", "content_item_ids", "counts"}."""
    batch_id = batch_id or uuid.uuid4().hex[:12]

    if _looks_like_url(source):
        uploads_dir = current_app.config["CONTENT_UPLOADS_DIR"]
        video_path = _download_youtube(source, uploads_dir)
        source_label = source
    else:
        if not os.path.exists(source):
            raise IngestError(f"Video file not found: {source}")
        video_path = source
        source_label = source

    segments = _transcribe(video_path, current_app.config["WHISPER_MODEL_SIZE"])
    if not segments:
        raise IngestError("Transcription returned no segments — is the audio track empty or silent?")

    transcript_text = _format_transcript(segments)
    plan = _plan_content(transcript_text, _apply_url())
    items = _persist_plan(plan, batch_id=batch_id, source_label=source_label)

    return {
        "batch_id": batch_id,
        "source_video": source_label,
        "content_item_ids": [i.id for i in items],
        "counts": _counts(items),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Local video file path or a YouTube URL")
    args = parser.parse_args()

    app = util.get_app()
    with app.app_context():
        try:
            result = ingest(args.source)
        except IngestError as e:
            print(f"Ingest failed: {e}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
