"""Badge print ordering for onboarding (Phase 6).

Implements the "local vendor's email order" option from the build brief:
composes a plain order email (participant names + quantities) and sends it
to PRINT_VENDOR_EMAIL via services/gmail_client.py. No print-vendor API key
required, no external account.

Future option (not built here): swap this for an API-based vendor like
Printful or Gelato once RPSAS picks one and wants automatic tracking/status
webhooks -- that would mean adding PRINTFUL_API_KEY (or similar) to
api/config.py and replacing the body of submit_badge_print_order() below
with a POST to that vendor's order-creation endpoint. Keeping this as a
one-function module makes that swap a self-contained change later.
"""

from collections import Counter

from flask import current_app

from services import gmail_client
from services.notify_utils import unsubscribe_url


def submit_badge_print_order(deal, participant_names):
    """Emails PRINT_VENDOR_EMAIL a badge print order for `deal`.
    `participant_names`: iterable of names (duplicates -> quantity > 1).
    Returns gmail_client.send_email()'s result dict, or None if there's
    nothing to order. Raises RuntimeError if PRINT_VENDOR_EMAIL isn't set."""
    vendor_email = current_app.config.get("PRINT_VENDOR_EMAIL")
    if not vendor_email:
        raise RuntimeError("PRINT_VENDOR_EMAIL not configured -- cannot submit badge print order.")

    counts = Counter(name for name in participant_names if name)
    if not counts:
        return None

    lead = deal.lead
    brand = current_app.config["BRAND_NAME"]
    org_suffix = f" / {lead.org}" if lead and lead.org else ""

    rows = "".join(
        f'<tr><td style="padding:6px 10px;border-bottom:1px solid #d9d3c6;">{name}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #d9d3c6;">{qty}</td></tr>'
        for name, qty in counts.items()
    )
    html_body = f"""
    <p>New {brand} METHOD badge print order for deal #{deal.id}
    ({lead.name if lead else "—"}{org_suffix}).</p>
    <table style="border-collapse:collapse;width:100%;max-width:480px;">
      <tr>
        <th style="text-align:left;padding:6px 10px;border-bottom:2px solid #17263B;">Participant</th>
        <th style="text-align:left;padding:6px 10px;border-bottom:2px solid #17263B;">Qty</th>
      </tr>
      {rows}
    </table>
    <p>Card size: CR80 (3.375in x 2.125in), design per the attached artwork
    (rendered via api/services/pdf/badge.py -- badge PDFs are available at
    /badge/&lt;participant_id&gt;.pdf on request).</p>
    <p>Ship to: {current_app.config["COMPANY_MAILING_ADDRESS"]}</p>
    """

    return gmail_client.send_email(
        to_email=vendor_email,
        subject=f"[Badge Order] {brand} Deal #{deal.id} — {lead.name if lead else 'Unknown'}",
        html_body=html_body,
        unsubscribe_url=unsubscribe_url(lead.id if lead else None),
    )
