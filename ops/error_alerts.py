"""One small helper: alert(subject, detail) -- lets other code notify the
founder when something fails silently (a sequence send failure, a webhook
signature failure logged more than N times, a backup that couldn't run,
etc). Deliberately not a monitoring system -- just this one function.

    from ops.error_alerts import alert
    try:
        risky_thing()
    except Exception as e:
        alert("Sequence send failed", str(e))
        raise  # or swallow, caller's call

Must run inside a Flask app context (current_app.config, and gmail_client's
own use of current_app).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from flask import current_app

from services import gmail_client


def alert(subject, detail=""):
    """Sends an email to FOUNDER_EMAIL via gmail_client. gmail_client only
    knows how to send HTML (see api/services/gmail_client.py), so `detail`
    is wrapped in a <pre>-like block rather than sent as true text/plain --
    still reads as plain text in an inbox. Returns gmail_client.send_email's
    result dict. Raises RuntimeError if FOUNDER_EMAIL isn't configured, so a
    misconfigured alert path fails loudly instead of silently dropping
    exactly the alerts it exists to surface."""
    founder_email = current_app.config.get("FOUNDER_EMAIL")
    if not founder_email:
        raise RuntimeError("FOUNDER_EMAIL not configured -- cannot send alert.")

    brand = current_app.config.get("BRAND_NAME", "RPSAS")
    escaped = (detail or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html_body = (
        f'<p style="font-family:-apple-system,sans-serif;color:#17263B;">'
        f"<strong>{subject}</strong></p>"
        f'<pre style="font-family:ui-monospace,Menlo,monospace;white-space:pre-wrap;'
        f'color:#17263B;background:#F4EFE6;padding:12px;border-radius:6px;">{escaped}</pre>'
    )
    site_base = current_app.config.get("SITE_BASE_URL", "")
    return gmail_client.send_email(
        to_email=founder_email,
        subject=f"[{brand} ALERT] {subject}",
        html_body=html_body,
        unsubscribe_url=f"{site_base}/",
    )
