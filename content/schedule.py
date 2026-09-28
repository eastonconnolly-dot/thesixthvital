"""content/schedule.py — queues approved ContentItems (status="approved")
for posting, via Buffer or Publer.

LinkedIn's own API cannot post to a personal profile on the founder's
behalf (only Company Pages, and only through a restricted partner program)
— that's why this goes through Buffer or Publer instead of calling
LinkedIn directly. Both are gated behind an env var (BUFFER_API_KEY /
PUBLER_API_KEY, api/config.py) and are entirely optional.

If neither key is configured, schedule_item() still marks the ContentItem
status="scheduled" locally — so the draft -> approve -> schedule workflow
is fully testable and demoable without a live Buffer/Publer account — but
nothing is actually pushed externally, and that fact is logged clearly
(never silently swallowed).

Nothing here ever schedules a ContentItem that isn't already
status="approved" — the only path to "approved" is the human approval gate
in api/routes/content_admin.py. This module cannot bypass it.

Usage:
    cd content && python schedule.py             # schedules every approved item
    cd content && python schedule.py --item 42    # schedules one item by id
"""

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # so `import util` resolves however this is imported

import util  # noqa: E402

util.add_api_to_path()

import requests  # noqa: E402
from flask import current_app  # noqa: E402

from extensions import db  # noqa: E402
from models import ContentItem  # noqa: E402

logger = logging.getLogger("content.schedule")

BUFFER_UPDATE_URL = "https://api.bufferapp.com/1/updates/create.json"
# Publer's scheduling endpoint varies by plan/workspace; this is the
# representative shape from their public API docs as of this writing —
# confirm against the account's actual workspace before relying on it.
PUBLER_POST_URL = "https://app.publer.io/api/v1/posts/schedule"

# ContentItem.type -> Buffer/Publer "profile group" tag. Left as a simple
# constant map rather than a DB table since there's exactly one destination
# (LinkedIn) named in the brief; extend here if that changes.
DEFAULT_CHANNEL = "linkedin"


class ScheduleError(Exception):
    """Raised when asked to schedule something that isn't approved yet, or
    when a configured Buffer/Publer call outright fails (as opposed to
    "not configured", which is a graceful no-op, not an error)."""


def _push_to_buffer(item, api_key):
    resp = requests.post(
        BUFFER_UPDATE_URL,
        data={"text": item.body, "access_token": api_key, "now": "false"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _push_to_publer(item, api_key):
    resp = requests.post(
        PUBLER_POST_URL,
        headers={"Authorization": f"Bearer-API {api_key}"},
        json={"content": {"text": item.body}, "type": DEFAULT_CHANNEL},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def schedule_item(item):
    """Marks one approved ContentItem as scheduled, pushing it to
    Buffer/Publer if a key is configured. Returns a dict describing what
    happened. Raises ScheduleError if the item isn't approved — this is the
    one place besides content_admin.py that touches ContentItem.status, and
    it refuses to move anything that didn't clear the approval gate."""
    if item.status != "approved":
        raise ScheduleError(
            f"ContentItem {item.id} is not approved (status={item.status!r}); refusing to schedule."
        )

    buffer_key = current_app.config.get("BUFFER_API_KEY")
    publer_key = current_app.config.get("PUBLER_API_KEY")

    pushed_to = None
    external_id = None
    if buffer_key:
        result = _push_to_buffer(item, buffer_key)
        pushed_to = "buffer"
        external_id = result.get("id") or result.get("update_id")
    elif publer_key:
        result = _push_to_publer(item, publer_key)
        pushed_to = "publer"
        external_id = result.get("id")
    else:
        logger.warning(
            "No BUFFER_API_KEY or PUBLER_API_KEY configured — ContentItem %s marked "
            "status=scheduled locally but was NOT pushed to any external scheduler.",
            item.id,
        )

    item.status = "scheduled"
    meta = dict(item.item_metadata or {})
    meta["scheduled_via"] = pushed_to
    meta["external_id"] = external_id
    item.item_metadata = meta
    db.session.commit()

    return {"content_item_id": item.id, "pushed_to": pushed_to, "external_id": external_id}


def schedule_all_approved():
    """Schedules every currently-approved ContentItem. Must be called
    inside a Flask app_context()."""
    items = ContentItem.query.filter_by(status="approved").all()
    return [schedule_item(item) for item in items]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--item", type=int, help="schedule a single ContentItem by id instead of all approved items")
    args = parser.parse_args()

    app = util.get_app()
    with app.app_context():
        try:
            if args.item:
                item = ContentItem.query.get(args.item)
                if item is None:
                    print(f"No ContentItem with id={args.item}", file=sys.stderr)
                    sys.exit(1)
                results = [schedule_item(item)]
            else:
                results = schedule_all_approved()
        except ScheduleError as e:
            print(f"Schedule failed: {e}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
