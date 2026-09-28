"""Outbound email via the Gmail API, with CAN-SPAM footer, unsubscribe link,
and thread-aware sending. Every send should be logged to the messages table
by the caller (see routes/public.py, outreach/engine)."""

import base64
from email.mime.text import MIMEText

from flask import current_app
from googleapiclient.discovery import build

from .google_auth import get_credentials


def _service():
    return build("gmail", "v1", credentials=get_credentials())


def _footer_html(unsubscribe_url):
    address = current_app.config["COMPANY_MAILING_ADDRESS"]
    brand = current_app.config["BRAND_NAME"]
    return (
        f'<p style="color:#8A97A8;font-size:12px;margin-top:24px;">'
        f"{brand} &middot; {address}<br/>"
        f'<a href="{unsubscribe_url}" style="color:#8A97A8;">Unsubscribe</a></p>'
    )


def send_email(to_email, subject, html_body, unsubscribe_url, thread_id=None, in_reply_to=None):
    """Returns {"message_id": str, "thread_id": str}. thread_id/in_reply_to
    let a sequence step reply into the same Gmail thread."""
    full_html = html_body + _footer_html(unsubscribe_url)
    msg = MIMEText(full_html, "html")
    msg["To"] = to_email
    msg["From"] = current_app.config["GMAIL_SENDER_EMAIL"]
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    body = {"raw": raw}
    if thread_id:
        body["threadId"] = thread_id

    service = _service()
    sent = service.users().messages().send(userId="me", body=body).execute()
    return {"message_id": sent["id"], "thread_id": sent["threadId"]}


def list_thread_replies(thread_id):
    """Used by reply-detection to see if a lead has replied to a thread."""
    service = _service()
    thread = service.users().threads().get(userId="me", id=thread_id).execute()
    return thread.get("messages", [])
