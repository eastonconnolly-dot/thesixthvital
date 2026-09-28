"""Monday 6am weekly ops digest, emailed to the founder.

Usage:
    flask send-digest                        # see register_cli() in api/app.py
    from ops.digest import build_digest, send_digest

`build_digest()` is a pure query function (no side effects) so it's easy to
unit test; `send_digest()` renders it to HTML and sends it via
api/services/gmail_client.py. Both must run inside a Flask app context
(current_app.config for FOUNDER_EMAIL / PRACTICE_PRICES, and the SQLAlchemy
session).

All datetime math is UTC-aware, matching models.utcnow(). Windows are
half-open [start, end) so consecutive weekly windows never double-count a
row that lands exactly on a boundary.
"""

import os
import sys
from datetime import date, datetime, timedelta, timezone

# So this is importable whether the caller already has api/ on sys.path
# (flask CLI, pytest via tests/conftest.py) or not.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from flask import current_app
from sqlalchemy import or_

from extensions import db
from models import Deal, Message, PracticeUser
from services import gmail_client

WINDOW_DAYS = 7

# PracticeUser.track values ("applicant" | "physician" | "program") don't
# match PRACTICE_PRICES' keys ("applicant" | "physician" | "program_seat")
# one-to-one -- see models.py's MICRO_LESSONS/PracticeUser and config.py's
# PRACTICE_PRICES. This bridges the naming mismatch for the MRR calc.
TRACK_TO_PRICE_KEY = {"applicant": "applicant", "physician": "physician", "program": "program_seat"}


def utcnow():
    return datetime.now(timezone.utc)


def _window(as_of):
    as_of = as_of or utcnow()
    current_end = as_of
    current_start = current_end - timedelta(days=WINDOW_DAYS)
    prior_end = current_start
    prior_start = prior_end - timedelta(days=WINDOW_DAYS)
    return {
        "current_start": current_start, "current_end": current_end,
        "prior_start": prior_start, "prior_end": prior_end,
    }


def _metric(current, prior, label, kind="count"):
    """kind: "count" (plain int), "cents" (format as $), or "na" (value that
    couldn't be computed -- rendered as "N/A", delta suppressed)."""
    if current is None or prior is None:
        return {"label": label, "kind": "na", "current": current, "prior": prior, "delta": None, "pct": None}
    delta = current - prior
    pct = round((delta / prior) * 100, 1) if prior else None
    return {"label": label, "kind": kind, "current": current, "prior": prior, "delta": delta, "pct": pct}


def _period_message_metrics(start, end):
    touches_sent = Message.query.filter(
        Message.direction == "outbound",
        Message.sent_at >= start, Message.sent_at < end,
    ).count()

    # A reply either arrives as its own logged inbound Message, or flips
    # `replied` on the original outbound one (inbox classifier, Phase 2) --
    # OR-ing both conditions in one filter counts each row once even if a
    # given row happens to satisfy both.
    replies = Message.query.filter(
        Message.sent_at >= start, Message.sent_at < end,
        or_(Message.direction == "inbound", Message.replied.is_(True)),
    ).count()

    return touches_sent, replies


def _period_deal_metrics(start, end):
    # "Calls booked" has no explicit event yet: Lead.status can be "booked"
    # per the state-machine comment in models.py, but nothing sets it, and
    # there's no booked_at timestamp to window a query on. Proxy: new Deals
    # opened in the window, since today every booked discovery call becomes
    # a Deal in "discovery" stage shortly after. Swap this for a real
    # Lead.booked_at (or a dedicated booking/calendar event model) once the
    # booking flow exists -- see ops/INTEGRATION.md.
    calls_booked = Deal.query.filter(Deal.created >= start, Deal.created < end).count()

    # `updated` is the best available proxy for "stage changed in this
    # window" (onupdate=utcnow on Deal) -- it isn't a dedicated stage-change
    # log, so a deal that has sat in "proposal_sent" for a while but had an
    # unrelated field touched this week would be (mis)counted here too.
    proposals_out = Deal.query.filter(
        Deal.stage == "proposal_sent",
        Deal.updated >= start, Deal.updated < end,
    ).count()

    closed = Deal.query.filter(
        Deal.stage.in_(("deposit_paid", "delivered")),
        Deal.updated >= start, Deal.updated < end,
    ).count()

    # Cash collected = 50% deposit on deals that newly went deposit_paid,
    # plus the balance on deals that newly went balance_paid, this window.
    newly_deposited = Deal.query.filter(
        Deal.deposit_paid.is_(True),
        Deal.deposit_paid_at.isnot(None),
        Deal.deposit_paid_at >= start, Deal.deposit_paid_at < end,
    ).all()
    deposit_cents = sum(round(d.amount_cents * 0.5) for d in newly_deposited)

    # balance_paid_at is new in this change (mirrors deposit_paid_at) --
    # routes/webhooks.py sets `balance_paid` but doesn't set this timestamp
    # yet (that file is out of scope for this change; see INTEGRATION.md),
    # so this will read 0 in practice until that one-line addition lands.
    newly_balanced = Deal.query.filter(
        Deal.balance_paid.is_(True),
        Deal.balance_paid_at.isnot(None),
        Deal.balance_paid_at >= start, Deal.balance_paid_at < end,
    ).all()
    balance_cents = sum(d.balance_due_cents or 0 for d in newly_balanced)

    return calls_booked, proposals_out, closed, deposit_cents + balance_cents


def _weeks_booked_asof(as_of):
    """Distinct ISO (year, week) among Deal.delivery_date values that are
    still in the future relative to `as_of`, counting only deals that
    already existed by `as_of` (so this can be reused to reconstruct last
    week's snapshot, for the week-over-week delta)."""
    as_of_date = as_of.date() if isinstance(as_of, datetime) else as_of
    rows = Deal.query.filter(
        Deal.delivery_date.isnot(None),
        Deal.delivery_date >= as_of_date,
        Deal.stage != "closed_lost",
        Deal.created <= as_of,
    ).all()
    weeks = {d.delivery_date.isocalendar()[:2] for d in rows}
    return len(weeks)


def _mrr_cents_asof(as_of, prices):
    """Sum of active PracticeUser subscriptions x their track's monthly
    price. Snapshot metric (current state), same as weeks-booked -- there's
    no subscription-status-change log, so the "prior" snapshot below is
    approximated using each user's *current* subscription_status filtered
    to users who already existed as of the prior window boundary. That
    misses churn/upgrades that happened mid-week; it's the best available
    signal without a status-history table."""
    users = PracticeUser.query.filter(
        PracticeUser.subscription_status == "active",
        PracticeUser.created <= as_of,
    ).all()
    cents = 0
    for u in users:
        price = prices.get(TRACK_TO_PRICE_KEY.get(u.track))
        if price:
            cents += price["amount_cents"]
    return cents


def build_digest(as_of=None):
    """Returns {"window": {...}, "metrics": {name: {label, kind, current,
    prior, delta, pct}, ...}}. Pure query function, no email side effects."""
    window = _window(as_of)
    cs, ce, ps, pe = window["current_start"], window["current_end"], window["prior_start"], window["prior_end"]

    metrics = {}

    touches_c, replies_c = _period_message_metrics(cs, ce)
    touches_p, replies_p = _period_message_metrics(ps, pe)
    metrics["touches_sent"] = _metric(touches_c, touches_p, "Touches sent")
    metrics["replies"] = _metric(replies_c, replies_p, "Replies")

    booked_c, proposals_c, closed_c, cash_c = _period_deal_metrics(cs, ce)
    booked_p, proposals_p, closed_p, cash_p = _period_deal_metrics(ps, pe)
    metrics["calls_booked"] = _metric(booked_c, booked_p, "Calls booked (proxy: new deals)")
    metrics["proposals_out"] = _metric(proposals_c, proposals_p, "Proposals out")
    metrics["closed"] = _metric(closed_c, closed_p, "Closed (deposit or delivered)")
    metrics["cash_collected_cents"] = _metric(cash_c, cash_p, "Cash collected", kind="cents")

    metrics["weeks_booked"] = _metric(_weeks_booked_asof(ce), _weeks_booked_asof(pe), "Calendar weeks booked (future)")

    try:
        trials_c = PracticeUser.query.filter(PracticeUser.created >= cs, PracticeUser.created < ce).count()
        trials_p = PracticeUser.query.filter(PracticeUser.created >= ps, PracticeUser.created < pe).count()
        metrics["app_trials"] = _metric(trials_c, trials_p, "App trials started")
    except Exception:
        current_app.logger.warning("digest: app_trials metric failed", exc_info=True)
        metrics["app_trials"] = _metric(None, None, "App trials started")

    try:
        prices = current_app.config["PRACTICE_PRICES"]
        metrics["mrr_cents"] = _metric(_mrr_cents_asof(ce, prices), _mrr_cents_asof(pe, prices), "MRR", kind="cents")
    except Exception:
        current_app.logger.warning("digest: mrr metric failed", exc_info=True)
        metrics["mrr_cents"] = _metric(None, None, "MRR", kind="cents")

    return {"window": window, "metrics": metrics}


def _fmt_cents(cents):
    return f"${cents / 100:,.2f}"


def _fmt_delta(m):
    if m["kind"] == "na" or m["delta"] is None:
        return ""
    sign = "+" if m["delta"] >= 0 else ""
    if m["kind"] == "cents":
        body = f"{sign}{_fmt_cents(m['delta'])}"
    else:
        body = f"{sign}{m['delta']}"
    if m["pct"] is not None:
        body += f" ({sign}{m['pct']}%)"
    return body


def _fmt_current(m):
    if m["kind"] == "na":
        return "N/A"
    if m["kind"] == "cents":
        return _fmt_cents(m["current"])
    return str(m["current"])


INK = "#17263B"
IVORY = "#F4EFE6"
AMBER = "#E0A458"
MUTED = "#8A97A8"

METRIC_ORDER = [
    "touches_sent", "replies", "calls_booked", "proposals_out", "closed",
    "cash_collected_cents", "weeks_booked", "app_trials", "mrr_cents",
]


def render_html(digest):
    brand = current_app.config.get("BRAND_NAME", "RPSAS")
    window = digest["window"]
    metrics = digest["metrics"]

    rows_html = ""
    for key in METRIC_ORDER:
        m = metrics[key]
        delta_str = _fmt_delta(m)
        delta_color = MUTED
        if m["delta"] is not None:
            delta_color = AMBER if m["delta"] > 0 else (MUTED if m["delta"] == 0 else "#B0413E")
        rows_html += f"""
          <tr>
            <td style="padding:12px 16px;border-bottom:1px solid rgba(23,38,59,0.12);font-family:-apple-system,BlinkMacSystemFont,'Source Sans 3',sans-serif;color:{INK};font-size:14px;">{m['label']}</td>
            <td style="padding:12px 16px;border-bottom:1px solid rgba(23,38,59,0.12);font-family:-apple-system,BlinkMacSystemFont,'Source Sans 3',sans-serif;color:{INK};font-size:16px;font-weight:600;text-align:right;">{_fmt_current(m)}</td>
            <td style="padding:12px 16px;border-bottom:1px solid rgba(23,38,59,0.12);font-family:-apple-system,BlinkMacSystemFont,'Source Sans 3',sans-serif;color:{delta_color};font-size:13px;text-align:right;white-space:nowrap;">{delta_str}</td>
          </tr>"""

    return f"""
    <div style="background:{IVORY};padding:32px 16px;font-family:-apple-system,BlinkMacSystemFont,'Source Sans 3',sans-serif;">
      <div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 12px 40px rgba(23,38,59,0.10);">
        <div style="background:{INK};padding:24px 28px;">
          <h1 style="margin:0;color:{IVORY};font-size:20px;font-weight:700;">{brand} — Weekly Ops Digest</h1>
          <p style="margin:6px 0 0;color:{AMBER};font-size:13px;">
            {window['current_start'].strftime('%b %d')} &ndash; {window['current_end'].strftime('%b %d, %Y')}
            &middot; vs. prior 7 days
          </p>
        </div>
        <table style="width:100%;border-collapse:collapse;">
          <thead>
            <tr>
              <th style="padding:10px 16px;text-align:left;font-size:11px;letter-spacing:0.04em;text-transform:uppercase;color:{MUTED};">Metric</th>
              <th style="padding:10px 16px;text-align:right;font-size:11px;letter-spacing:0.04em;text-transform:uppercase;color:{MUTED};">This week</th>
              <th style="padding:10px 16px;text-align:right;font-size:11px;letter-spacing:0.04em;text-transform:uppercase;color:{MUTED};">vs. last week</th>
            </tr>
          </thead>
          <tbody>{rows_html}
          </tbody>
        </table>
        <div style="padding:16px 28px;color:{MUTED};font-size:12px;">
          Generated automatically every Monday at 6am. See ops/digest.py.
        </div>
      </div>
    </div>
    """


def send_digest(as_of=None):
    """Builds the digest and emails it to FOUNDER_EMAIL via gmail_client.
    Returns gmail_client.send_email's result dict."""
    founder_email = current_app.config.get("FOUNDER_EMAIL")
    if not founder_email:
        raise RuntimeError("FOUNDER_EMAIL not configured -- set it before running `flask send-digest`.")

    digest = build_digest(as_of=as_of)
    html = render_html(digest)
    brand = current_app.config.get("BRAND_NAME", "RPSAS")
    week_of = digest["window"]["current_start"].date().isoformat()
    subject = f"{brand} Weekly Digest — week of {week_of}"

    # This is an internal, founder-to-self operational email, not marketing
    # outreach -- gmail_client.send_email always appends the CAN-SPAM
    # unsubscribe footer regardless (reused as-is per the brief, not
    # rewritten), so this just points it at the marketing site.
    site_base = current_app.config.get("SITE_BASE_URL", "")
    return gmail_client.send_email(
        to_email=founder_email,
        subject=subject,
        html_body=html,
        unsubscribe_url=f"{site_base}/",
    )
