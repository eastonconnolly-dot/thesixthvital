"""content/send_newsletter.py — sends the current week's approved weekly
newsletter to the nurture list.

Finds the ContentItem with type="newsletter" and status in
("approved", "scheduled") whose item_metadata.send_week matches the
current ISO week (Thursday send, per the brief — see content/ingest.py's
util.next_thursday_iso_week()), and sends it via services.gmail_client to
every Lead with status in ("nurture", "qualified"), skipping anyone on the
suppression list (models.SuppressedEmail — bounced/unsubscribed/manual).
gmail_client.send_email() already appends the CAN-SPAM footer and
unsubscribe link, so there's no separate compliance-footer logic here.

Never sends a newsletter that hasn't cleared the approval gate in
api/routes/content_admin.py — a "draft" ContentItem is invisible to
find_current_newsletter().

Usage:
    cd content && python send_newsletter.py              # sends this week's approved newsletter
    cd content && python send_newsletter.py --dry-run     # lists recipients without sending
    cd content && python send_newsletter.py --week 2026-W41
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # so `import util` resolves however this is imported

import util  # noqa: E402

util.add_api_to_path()

from flask import current_app  # noqa: E402

from extensions import db  # noqa: E402
from models import ContentItem, Lead, SuppressedEmail  # noqa: E402
from services import gmail_client  # noqa: E402

NURTURE_STATUSES = ("nurture", "qualified")
SENDABLE_STATUSES = ("approved", "scheduled")


class NewsletterSendError(Exception):
    """Raised when there's nothing approved to send — never for a delivery
    failure partway through (those are per-recipient and reported, not
    fatal, since one bad address shouldn't block the rest of the list)."""


def find_current_newsletter(week=None):
    """Returns the approved/scheduled newsletter ContentItem tagged for
    this ISO week, or None. Deliberately ignores drafts — an ingested but
    unapproved newsletter is invisible here."""
    week = week or util.current_iso_week()
    candidates = ContentItem.query.filter(
        ContentItem.type == "newsletter", ContentItem.status.in_(SENDABLE_STATUSES)
    ).all()
    for item in candidates:
        if (item.item_metadata or {}).get("send_week") == week:
            return item
    return None


def _to_html(body_text):
    paragraphs = (body_text or "").split("\n\n")
    return "".join(f"<p>{p.strip()}</p>" for p in paragraphs if p.strip())


def _recipients():
    suppressed = {row.email for row in SuppressedEmail.query.all()}
    leads = Lead.query.filter(Lead.status.in_(NURTURE_STATUSES)).all()
    return [lead for lead in leads if lead.email not in suppressed]


def send_newsletter(item=None, week=None, dry_run=False):
    """Sends (or, with dry_run=True, previews) the current week's approved
    newsletter to the nurture list. Must be called inside a Flask
    app_context(). Returns {"content_item_id", "recipients": [...]}."""
    item = item or find_current_newsletter(week)
    if item is None:
        raise NewsletterSendError(
            f"No approved newsletter ContentItem found for week {week or util.current_iso_week()}. "
            "Approve one in /admin/content first."
        )

    subject = (item.item_metadata or {}).get("subject") or item.title or f"{current_app.config['BRAND_NAME']} Newsletter"
    html_body = _to_html(item.body)

    sent = []
    for lead in _recipients():
        if dry_run:
            sent.append({"lead_id": lead.id, "email": lead.email, "dry_run": True})
            continue
        unsubscribe_url = f"{current_app.config['API_BASE_URL']}/unsubscribe?lead_id={lead.id}"
        result = gmail_client.send_email(
            to_email=lead.email,
            subject=subject,
            html_body=html_body,
            unsubscribe_url=unsubscribe_url,
        )
        sent.append({"lead_id": lead.id, "email": lead.email, "message_id": result["message_id"]})

    if not dry_run:
        meta = dict(item.item_metadata or {})
        meta["sent_at"] = datetime.now(timezone.utc).isoformat()
        meta["sent_count"] = len(sent)
        item.item_metadata = meta
        item.status = "published"
        db.session.commit()

    return {"content_item_id": item.id, "recipients": sent}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", help="ISO week to send for, e.g. 2026-W41 (default: current week)")
    parser.add_argument("--dry-run", action="store_true", help="list recipients without sending")
    args = parser.parse_args()

    app = util.get_app()
    with app.app_context():
        try:
            result = send_newsletter(week=args.week, dry_run=args.dry_run)
        except NewsletterSendError as e:
            print(f"Newsletter send failed: {e}", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
