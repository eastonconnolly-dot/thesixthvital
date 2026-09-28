"""Public one-click unsubscribe endpoint -- the actual landing page for the
link every `gmail_client.send_email()` call embeds in its CAN-SPAM footer
(see `outreach/engine/sequences.py`'s `_send_email_step`, `api/routes/inbox.py`'s
`send_reply`, and `content/send_newsletter.py`, all of which build a
`{API_BASE_URL}/unsubscribe?lead_id=...` URL but -- until this file -- had
nothing on the other end of it). A standalone blueprint (not folded into
`routes/public.py`, which another engineer is actively working in) so it can
be registered independently; see `outreach/INTEGRATION.md`.

One click suppresses: no login, no second confirmation step, matching how
every real-world unsubscribe link behaves (and avoiding the classic
mistake of requiring a POST/confirmation click, which some mail clients'
link-prefetching would trigger accidentally anyway).
"""

from flask import Blueprint, current_app, request

from extensions import db
from models import Lead, SequenceEnrollment, SuppressedEmail

bp = Blueprint("unsubscribe", __name__)

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/><title>Unsubscribed — {brand}</title>
<style>
  body {{ margin:0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background:#F4EFE6; color:#17263B; display:flex; align-items:center; justify-content:center; min-height:100vh; }}
  .card {{ background:#fff; border-radius:12px; padding:32px 40px; max-width:420px; text-align:center;
          box-shadow:0 4px 16px rgba(23,38,59,0.08); }}
  h1 {{ font-family: Georgia, serif; font-size:1.4rem; }}
  p {{ color:#8A97A8; }}
</style></head>
<body><div class="card"><h1>{heading}</h1><p>{message}</p></div></body></html>"""


@bp.route("/unsubscribe", methods=["GET", "POST"])
def unsubscribe():
    lead_id = request.args.get("lead_id", type=int)
    brand = current_app.config["BRAND_NAME"]

    lead = db.session.get(Lead, lead_id) if lead_id else None
    if not lead:
        return _PAGE.format(
            brand=brand, heading="Link not recognized",
            message="This unsubscribe link is missing or invalid — nothing was changed.",
        ), 400

    existing = SuppressedEmail.query.filter_by(email=lead.email.strip().lower()).first()
    if not existing:
        db.session.add(SuppressedEmail(email=lead.email.strip().lower(), reason="unsubscribed"))

    for enrollment in SequenceEnrollment.query.filter_by(lead_id=lead.id, status="active").all():
        enrollment.status = "stopped"
        enrollment.stop_reason = "unsubscribed"

    lead.status = "unsubscribed"
    db.session.commit()

    return _PAGE.format(
        brand=brand, heading="You're unsubscribed",
        message=f"{lead.email} won't receive any further emails from {brand}.",
    )
