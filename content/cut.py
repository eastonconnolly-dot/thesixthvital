"""content/cut.py — ffmpeg wrapper that turns each selected clip window
(a ContentItem with type="clip", item_metadata carrying source_video/start/end)
into two brand-styled crops — 9:16 (Reels/Shorts/TikTok) and 1:1 (feed) —
with burned-in captions, and records the resulting files as Asset rows
linked to the ContentItem.

Shells out to the `ffmpeg` binary via subprocess. If `ffmpeg` isn't on PATH,
this raises a clear CutError telling you how to install it instead of
crashing with a raw FileNotFoundError.

Captions use the same "don't assume specific fonts are on the render host"
caution as api/services/pdf/base.py: if the real brand font
(shared/brand/fonts/SourceSans3-SemiBold.ttf) has been dropped in, ffmpeg's
drawtext uses it directly (fontfile=); otherwise it falls back to a generic
fontconfig family name that resolves to whatever sans-serif is actually
installed on the host.

Usage:
    cd content && python cut.py <batch_id>   # cuts every clip ContentItem in that batch
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # so `import util` resolves however this is imported

import util  # noqa: E402

util.add_api_to_path()

from flask import current_app  # noqa: E402

from extensions import db  # noqa: E402
from models import Asset, ContentItem  # noqa: E402

FFMPEG_BIN = "ffmpeg"
MAX_CAPTION_CHARS = 140
# Both crop targets scale to 1080px wide (see CROPS below). At fontsize=54,
# an unwrapped caption overflows the frame the moment it's much longer than
# a short phrase -- discovered live (a real ~83-char hook rendered as a
# single line ran off both edges). ~27px/char average for a proportional
# sans face at this size, so 28 chars/line stays safely inside 1080px with
# the box's own padding.
CAPTION_WRAP_CHARS = 28

INK = "0x17263B"
IVORY = "0xF4EFE6"

_FONTS_DIR = os.path.join(os.path.dirname(__file__), "..", "shared", "brand", "fonts")
CAPTION_FONTFILE = os.path.join(_FONTS_DIR, "SourceSans3-SemiBold.ttf")
CAPTION_FALLBACK_FAMILY = "Sans"  # generic fontconfig alias present on virtually every ffmpeg build with fontconfig

# (label, ffmpeg filter, output suffix) — crop-then-scale, centered by default.
CROPS = {
    "9x16": {"filter": "crop=ih*9/16:ih,scale=1080:1920", "suffix": "9x16"},
    "1x1": {"filter": "crop=ih:ih,scale=1080:1080", "suffix": "1x1"},
}


class CutError(Exception):
    """Raised for anything that should stop cutting with a clear message
    rather than a raw traceback or an opaque ffmpeg stderr dump."""


def _ffmpeg_available():
    return shutil.which(FFMPEG_BIN) is not None


def _escape_drawtext(text):
    """Escapes the handful of characters ffmpeg's drawtext filter treats
    specially inside a filtergraph string."""
    return (
        (text or "")
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "’")  # drawtext can't escape a literal single quote inside text='...'; use a typographic apostrophe instead
        .replace("%", "\\%")
        .replace("\n", " ")
    )


def _run_ffmpeg(args):
    """Runs `ffmpeg` with the given args, raising CutError with the
    captured stderr on failure. Isolated as its own function so tests can
    mock it without a real ffmpeg binary or video file on disk."""
    if not _ffmpeg_available():
        raise CutError(
            "The `ffmpeg` binary was not found on PATH. Install it (e.g. `brew install "
            "ffmpeg` on macOS or `apt-get install ffmpeg` on Debian/Ubuntu) before running "
            "content/cut.py."
        )
    result = subprocess.run([FFMPEG_BIN, *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise CutError(f"ffmpeg failed (exit {result.returncode}): {result.stderr[-2000:]}")
    return result


def _font_clause():
    if os.path.exists(CAPTION_FONTFILE):
        return f"fontfile={CAPTION_FONTFILE}"
    return f"font={CAPTION_FALLBACK_FAMILY}"


def _drawtext_filter(caption_text):
    truncated = (caption_text or "")[:MAX_CAPTION_CHARS]
    lines = textwrap.wrap(truncated, width=CAPTION_WRAP_CHARS) or [""]
    # Join with backslash + an ACTUAL newline byte, not the two characters
    # "\" + "n". Verified live against a real render: ffmpeg's filtergraph
    # string parser (which parses the whole -vf argument before drawtext
    # ever sees the text= value) treats a bare backslash-n as "escaped
    # literal n" -- it renders a literal "n" in the caption and swallows the
    # backslash, not a line break. Escaping a real newline byte is what the
    # filtergraph parser actually passes through as a newline. subprocess.run
    # passes args with no shell involved, so this newline byte reaches
    # ffmpeg's own argv unchanged either way.
    wrapped = "\\\n".join(_escape_drawtext(line) for line in lines)
    return (
        f"drawtext=text='{wrapped}':{_font_clause()}:fontsize=54:fontcolor={IVORY}:"
        f"box=1:boxcolor={INK}@0.75:boxborderw=18:x=(w-text_w)/2:y=h-th-120:line_spacing=6"
    )


def cut_clip(content_item, output_dir=None):
    """Produces the 9:16 and 1:1 brand-styled crops for one clip
    ContentItem, records them as Asset rows, and returns the list of
    created Asset objects. Must be called inside a Flask app_context()."""
    if content_item.type != "clip":
        raise CutError(f"ContentItem {content_item.id} is not a clip (type={content_item.type!r}).")

    meta = content_item.item_metadata or {}
    source = meta.get("source_video")
    start, end = meta.get("start"), meta.get("end")
    if not source or start is None or end is None:
        raise CutError(
            f"ContentItem {content_item.id} is missing source_video/start/end in item_metadata "
            "— was it created by content/ingest.py?"
        )
    duration = max(0.1, float(end) - float(start))

    output_dir = output_dir or current_app.config["CONTENT_CLIPS_DIR"]
    os.makedirs(output_dir, exist_ok=True)

    caption_text = meta.get("hook") or content_item.title or ""
    drawtext = _drawtext_filter(caption_text)

    created = []
    for crop in CROPS.values():
        out_path = os.path.join(output_dir, f"content_item_{content_item.id}_{crop['suffix']}.mp4")
        vf = f"{crop['filter']},{drawtext}"
        args = [
            "-y",
            "-ss", str(start),
            "-i", source,
            "-t", str(duration),
            "-vf", vf,
            "-c:v", "libx264",
            "-c:a", "aac",
            out_path,
        ]
        _run_ffmpeg(args)
        asset = Asset(content_item_id=content_item.id, kind="video", path=out_path)
        db.session.add(asset)
        created.append(asset)

    db.session.commit()
    return created


def cut_batch(batch_id, output_dir=None):
    """Cuts every clip ContentItem tagged with this batch_id (set by
    content/ingest.py). Returns a summary dict."""
    clips = [
        item for item in ContentItem.query.filter_by(type="clip").all()
        if (item.item_metadata or {}).get("batch_id") == batch_id
    ]
    if not clips:
        raise CutError(f"No clip ContentItems found for batch_id={batch_id!r}.")

    assets_by_item = {}
    for item in clips:
        assets = cut_clip(item, output_dir=output_dir)
        assets_by_item[item.id] = [a.path for a in assets]

    return {"batch_id": batch_id, "clips_cut": len(clips), "assets": assets_by_item}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_id", help="batch_id of an ingest run (see content/ingest.py output)")
    args = parser.parse_args()

    app = util.get_app()
    with app.app_context():
        try:
            result = cut_batch(args.batch_id)
        except CutError as e:
            print(f"Cut failed: {e}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
