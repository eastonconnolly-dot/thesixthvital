"""content_inventory.py — Phase 6, "Content on inventory, not on the
founder": batch-ingest a quarterly filming day's worth of source videos,
estimate how many days of scheduled content remain, and alert the founder
(once per drop, not on every check) when the queue runs low.

    batch_ingest(video_paths)          -- run content/ingest.py once per path
    ingest_consented_clips()           -- defensive integration point, see below
    days_of_inventory_remaining()      -- an estimate; see its docstring
    check_inventory_and_alert()        -- the thing `flask check-content-inventory` runs

This module deliberately does not import content/ingest.py at module import
time -- content/ isn't a Flask-app-importable package (no __init__.py; see
content/util.py's own docstring), so batch_ingest() adds content/ to
sys.path and imports it lazily, the same way api/tests/test_content_ingest.py
does.
"""

import os
import sys

from flask import current_app

from extensions import db
from models import ContentItem, InventoryAlertLog

INVENTORY_THRESHOLD_DAYS = 30

# Phase 3's content/ingest.py produces exactly 5 LinkedIn posts per source
# video (one per weekday, WEEKDAYS in content/ingest.py), and the founder's
# cadence for publishing them is 5/week -- see days_of_inventory_remaining().
LINKEDIN_POSTS_PER_WEEK = 5
DAYS_PER_WEEK = 7


def _content_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "content")


def _content_ingest_module():
    """Lazily imports content/ingest.py. Adds content/ to sys.path first
    (mirrors content/util.py::add_api_to_path()'s own approach, just in the
    other direction) so `import ingest` resolves regardless of cwd."""
    content_dir = os.path.normpath(_content_dir())
    if content_dir not in sys.path:
        sys.path.insert(0, content_dir)
    import ingest  # noqa: E402  (content/ingest.py)
    return ingest


def batch_ingest(video_paths):
    """Runs content/ingest.py's ingest() once per path in video_paths -- the
    "8 videos ingested at once" quarterly batch-filming-day flow. Tolerates
    any single video failing (bad file, transcription error, malformed
    Claude response, etc.) without aborting the rest of the batch: every
    path gets its own try/except, and the summary records exactly which
    ones succeeded and which failed and why.

    Must be called inside a Flask app_context() (content/ingest.ingest()
    requires current_app).

    Returns {"total": int, "succeeded": int, "failed": int, "results": [
        {"source": str, "ok": bool, "result": dict}  (on success), or
        {"source": str, "ok": False, "error": str}    (on failure)
    ]}.
    """
    ingest_mod = _content_ingest_module()

    results = []
    for source in video_paths:
        try:
            result = ingest_mod.ingest(source)
            results.append({"source": source, "ok": True, "result": result})
        except ingest_mod.IngestError as e:
            results.append({"source": source, "ok": False, "error": str(e)})
        except Exception as e:  # belt-and-suspenders: one bad video must never sink the batch
            results.append({"source": source, "ok": False, "error": f"Unexpected error: {e}"})

    succeeded = sum(1 for r in results if r["ok"])
    return {
        "total": len(video_paths),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }


def ingest_consented_clips():
    """Integration point for "automatic ingestion of consented clips from
    intensives" (build brief, Phase 6). This depends on the clip-consent
    request/response tracking another engineer is building in parallel as
    part of the post-delivery automation work.

    As of this writing, `models.ConsentRequest` already exists (kind in
    ("testimonial", "clip_consent"), a `granted` boolean, an `approved`
    founder-review boolean, and a `session_id` FK to EncounterSession) --
    but it does not yet carry a video/clip *file path* anywhere reachable
    from it, so there's genuinely nothing to hand to batch_ingest() yet.
    Rather than guess at a field name that might not match what eventually
    lands, this function queries defensively:

      1. Imports ConsentRequest inside a try/except ImportError, so this
         no-ops cleanly (with a clear log message) if that model doesn't
         exist at all yet.
      2. Filters to granted clip-consent rows only if the expected columns
         (`kind`, `granted`) are present (via hasattr), rather than
         assuming their exact semantics.
      3. Looks for a clip/video path on each row by trying a short list of
         plausible attribute names in order (`clip_path`, `video_path`,
         `file_path`, `clip_file_path`, `recording_path`) via getattr,
         so this self-heals -- starts actually ingesting -- the moment a
         path field is added, with no code change required here.

    Returns a dict describing what happened; never raises for the "not
    ready yet" cases:
        {"available": False, "reason": str, "ingested": None}
            ConsentRequest doesn't exist yet.
        {"available": True, "ingested": 0, "summary": None,
         "reason": "<why nothing was ingested>"}
            ConsentRequest exists, but there's nothing to ingest yet
            (no granted clip consents, or no path field found on them).
        {"available": True, "ingested": N, "summary": {...batch_ingest()...}}
            Real clips were found and handed to batch_ingest().
    """
    try:
        from models import ConsentRequest
    except ImportError:
        msg = (
            "content_inventory.ingest_consented_clips: models.ConsentRequest "
            "is not defined yet (Phase 6 post-delivery/consent-tracking work "
            "hasn't landed) -- no-op."
        )
        current_app.logger.info(msg)
        return {"available": False, "reason": "ConsentRequest model not defined", "ingested": None}

    query = ConsentRequest.query
    if hasattr(ConsentRequest, "kind"):
        query = query.filter_by(kind="clip_consent")
    if hasattr(ConsentRequest, "granted"):
        query = query.filter_by(granted=True)
    candidates = query.all()

    PATH_ATTRS = ("clip_path", "video_path", "file_path", "clip_file_path", "recording_path")
    paths = []
    for row in candidates:
        path = None
        for attr in PATH_ATTRS:
            path = getattr(row, attr, None)
            if path:
                break
        if path:
            paths.append(path)

    if not candidates:
        return {"available": True, "ingested": 0, "summary": None, "reason": "no granted clip-consent rows yet"}
    if not paths:
        msg = (
            f"content_inventory.ingest_consented_clips: found {len(candidates)} granted "
            f"clip-consent row(s) but none carries a recognized clip/video path field "
            f"({', '.join(PATH_ATTRS)}) yet -- no-op until that lands."
        )
        current_app.logger.info(msg)
        return {
            "available": True, "ingested": 0, "summary": None,
            "reason": "granted clip-consent rows found, but none has a usable clip path field yet",
        }

    summary = batch_ingest(paths)
    return {"available": True, "ingested": len(paths), "summary": summary}


def days_of_inventory_remaining():
    """Estimates how many days of scheduled content remain queued.

    This is a genuine estimate, not an exact science -- documented formula:

        count = number of ContentItem rows with type="linkedin_post" and
                status in ("approved", "scheduled")   (i.e. not yet published,
                but cleared to go out)
        days_remaining = count / (LINKEDIN_POSTS_PER_WEEK / DAYS_PER_WEEK)
                        = count * 7 / 5

    Why LinkedIn posts specifically: content/ingest.py's per-batch content
    plan always produces exactly 5 LinkedIn posts (one per weekday) per
    source video, and LinkedIn is the cadence-bound channel the build brief
    calls out (5 posts/week) -- clips/newsletters/captions/proof snippets
    ride along with each video but aren't independently scheduled on a
    fixed weekly cadence the same way, so they're not part of this estimate.

    Caveats, spelled out rather than hidden behind a clean number:
      - Assumes every queued post is eventually published in order at
        exactly the standard 5/week cadence -- ignores content/schedule.py's
        actual push cursor/history.
      - Doesn't account for a post getting rejected between "approved" and
        "published", or for the founder skipping a week.
      - Doesn't factor in other content types (clips, newsletters) as
        "inventory" even though they're part of what a video batch produces.
      - Returns 0.0 (not a negative number) when there's no queued content.

    Treat this as a founder-facing early-warning signal, not a scheduling
    source of truth.
    """
    count = ContentItem.query.filter(
        ContentItem.type == "linkedin_post",
        ContentItem.status.in_(("approved", "scheduled")),
    ).count()
    if count <= 0:
        return 0.0
    return count * DAYS_PER_WEEK / LINKEDIN_POSTS_PER_WEEK


def check_inventory_and_alert():
    """Computes days_of_inventory_remaining() and, if it's under
    INVENTORY_THRESHOLD_DAYS (30), alerts the founder via
    ops.error_alerts.alert() -- but only once per drop-below-threshold
    event, tracked via InventoryAlertLog:

      - If inventory is currently below threshold and there's no *active*
        (unresolved) alert row yet, send one and record it.
      - If inventory is currently below threshold and an alert row is
        already active, do nothing (already alerted; don't spam every time
        this cron runs while inventory stays low).
      - If inventory is currently at/above threshold and there's an active
        alert row, mark it resolved -- this re-arms alerting for the next
        time inventory drops.

    Must run inside a Flask app_context() (ops.error_alerts.alert() needs
    current_app + a working gmail_client). Returns
    {"days_remaining": float, "alerted": bool, "threshold": int}.
    """
    days_remaining = days_of_inventory_remaining()
    active_alert = (
        InventoryAlertLog.query
        .filter_by(resolved_at=None)
        .order_by(InventoryAlertLog.sent_at.desc())
        .first()
    )

    alerted = False
    if days_remaining < INVENTORY_THRESHOLD_DAYS:
        if active_alert is None:
            from ops.error_alerts import alert
            alert(
                "Content inventory below 30 days",
                f"Estimated {days_remaining:.1f} days of scheduled LinkedIn content remain "
                f"(threshold: {INVENTORY_THRESHOLD_DAYS} days). Time to run a batch-filming day "
                f"or check content/ingest.py output -- see api/services/content_inventory.py "
                f"for exactly how this estimate is computed.",
            )
            db.session.add(InventoryAlertLog(days_remaining_at_send=days_remaining))
            db.session.commit()
            alerted = True
        # else: already alerted for this drop, still below -- no-op.
    else:
        if active_alert is not None:
            from models import utcnow
            active_alert.resolved_at = utcnow()
            db.session.commit()

    return {"days_remaining": days_remaining, "alerted": alerted, "threshold": INVENTORY_THRESHOLD_DAYS}
