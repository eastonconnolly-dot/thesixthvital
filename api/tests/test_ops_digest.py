"""ops/digest.py -- exact-value tests for the weekly metrics + week-over-week
deltas. Uses a fixed `as_of` so window boundaries are deterministic instead
of depending on when the suite happens to run."""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))  # repo root, for `ops.*`

from extensions import db
from models import Deal, Lead, Message, PracticeUser
from ops.digest import build_digest, send_digest

AS_OF = datetime(2026, 9, 28, 6, 0, 0, tzinfo=timezone.utc)
CURR_END = AS_OF
CURR_START = AS_OF - timedelta(days=7)
PRIOR_END = CURR_START
PRIOR_START = CURR_START - timedelta(days=7)


def _lead(email="lead@example.com", track="physician"):
    lead = Lead(name="Test Lead", email=email, track=track)
    db.session.add(lead)
    db.session.flush()
    return lead


def _seed_full_dataset():
    lead = _lead()

    # ── Messages: touches_sent + replies ─────────────────────────────
    msgs = [
        # (direction, replied, sent_at)
        ("outbound", False, CURR_END - timedelta(days=1)),   # touch, current
        ("outbound", True, CURR_END - timedelta(days=2)),    # touch + reply(via replied flag), current
        ("inbound", False, CURR_END - timedelta(days=3)),    # reply(inbound), current
        ("outbound", False, CURR_END - timedelta(days=4)),   # touch, current
        ("outbound", False, PRIOR_END - timedelta(days=1)),  # touch, prior
        ("inbound", False, PRIOR_END - timedelta(days=2)),   # reply(inbound), prior
        ("outbound", False, AS_OF - timedelta(days=30)),     # outside both windows
    ]
    for direction, replied, sent_at in msgs:
        db.session.add(Message(lead_id=lead.id, direction=direction, replied=replied, sent_at=sent_at))

    # ── Deals: calls_booked (proxy), proposals_out, closed, cash_collected ──
    # calls_booked proxy = Deal.created in window
    db.session.add(Deal(lead_id=lead.id, package="physician_private", amount_cents=100,
                         balance_due_cents=0, stage="discovery", created=CURR_END - timedelta(days=1)))
    db.session.add(Deal(lead_id=lead.id, package="physician_private", amount_cents=100,
                         balance_due_cents=0, stage="discovery", created=CURR_END - timedelta(days=5)))
    db.session.add(Deal(lead_id=lead.id, package="physician_private", amount_cents=100,
                         balance_due_cents=0, stage="discovery", created=PRIOR_END - timedelta(days=1)))

    # proposals_out = stage == proposal_sent, `updated` in window. `created`
    # is deliberately pinned well outside BOTH windows (unlike the deals
    # above) so these don't also leak into the calls_booked proxy count,
    # which filters on `created` alone -- keeps that metric's expected value
    # unambiguous instead of needing to account for every other deal below.
    OUTSIDE = AS_OF - timedelta(days=40)
    db.session.add(Deal(lead_id=lead.id, package="physician_private", amount_cents=100,
                         balance_due_cents=0, stage="proposal_sent",
                         created=OUTSIDE, updated=CURR_END - timedelta(days=1)))
    db.session.add(Deal(lead_id=lead.id, package="physician_private", amount_cents=100,
                         balance_due_cents=0, stage="proposal_sent",
                         created=OUTSIDE, updated=PRIOR_END - timedelta(days=1)))

    # closed = stage in (deposit_paid, delivered), `updated` in window
    db.session.add(Deal(lead_id=lead.id, package="physician_private", amount_cents=1_000_000,
                         balance_due_cents=0, stage="deposit_paid", deposit_paid=True,
                         deposit_paid_at=CURR_END - timedelta(days=2),
                         created=OUTSIDE, updated=CURR_END - timedelta(days=2)))
    db.session.add(Deal(lead_id=lead.id, package="physician_private", amount_cents=100,
                         balance_due_cents=0, stage="delivered",
                         created=OUTSIDE, updated=PRIOR_END - timedelta(days=2)))

    # cash_collected extras, deliberately isolated from the `closed` metric by
    # keeping `updated` outside both windows (so they don't double-count there)
    db.session.add(Deal(lead_id=lead.id, package="physician_private", amount_cents=100,
                         balance_due_cents=300_000, stage="delivered", balance_paid=True,
                         balance_paid_at=CURR_END - timedelta(days=1),
                         created=OUTSIDE, updated=OUTSIDE))
    db.session.add(Deal(lead_id=lead.id, package="physician_private", amount_cents=2_000_000,
                         balance_due_cents=0, stage="deposit_paid", deposit_paid=True,
                         deposit_paid_at=PRIOR_END - timedelta(days=1),
                         created=OUTSIDE, updated=OUTSIDE))

    # ── Deals: weeks_booked (future delivery_date snapshot) ──────────
    def _dated_deal(delivery_date, stage, created):
        return Deal(lead_id=lead.id, package="physician_private", amount_cents=100,
                    balance_due_cents=0, stage=stage, delivery_date=delivery_date, created=created)

    old_created = AS_OF - timedelta(days=30)
    db.session.add(_dated_deal(date_(2026, 10, 5), "scheduled", old_created))   # week A
    db.session.add(_dated_deal(date_(2026, 10, 6), "scheduled", old_created))   # week A (dedup)
    db.session.add(_dated_deal(date_(2026, 11, 16), "scheduled", old_created))  # week B
    db.session.add(_dated_deal(date_(2026, 9, 25), "scheduled", old_created))   # week C: future for prior snapshot only
    # week D: didn't exist as of the prior snapshot (created after PRIOR_END).
    # Its `created` also falls inside the current 7-day window, so -- like
    # any other deal -- it also adds +1 to calls_booked's current count;
    # accounted for in that assertion below rather than hidden.
    db.session.add(_dated_deal(date_(2026, 10, 20), "scheduled", AS_OF - timedelta(days=1)))
    db.session.add(_dated_deal(date_(2026, 12, 25), "closed_lost", old_created))  # excluded: closed_lost

    # ── PracticeUsers: app_trials + MRR ───────────────────────────────
    db.session.add(PracticeUser(email="u1@example.com", track="physician", subscription_status="active",
                                 created=CURR_END - timedelta(days=1)))
    db.session.add(PracticeUser(email="u2@example.com", track="applicant", subscription_status="active",
                                 created=PRIOR_END - timedelta(days=1)))
    db.session.add(PracticeUser(email="u3@example.com", track="program", subscription_status="active",
                                 created=AS_OF - timedelta(days=20)))
    db.session.add(PracticeUser(email="u4@example.com", track="physician", subscription_status="trialing",
                                 created=CURR_END - timedelta(days=2)))
    db.session.add(PracticeUser(email="u5@example.com", track="some_unmapped_track", subscription_status="active",
                                 created=CURR_END - timedelta(days=3)))

    db.session.commit()


def date_(y, m, d):
    from datetime import date
    return date(y, m, d)


def test_build_digest_computes_exact_metrics_and_deltas(app):
    with app.app_context():
        _seed_full_dataset()
        digest = build_digest(as_of=AS_OF)
        m = digest["metrics"]

        assert m["touches_sent"]["current"] == 3
        assert m["touches_sent"]["prior"] == 1
        assert m["touches_sent"]["delta"] == 2
        assert m["touches_sent"]["pct"] == round((2 / 1) * 100, 1)

        assert m["replies"]["current"] == 2
        assert m["replies"]["prior"] == 1
        assert m["replies"]["delta"] == 1

        # 2 explicit "discovery" deals + the week-D deal from the
        # weeks_booked dataset below (its `created` also lands in-window)
        assert m["calls_booked"]["current"] == 3
        assert m["calls_booked"]["prior"] == 1
        assert m["calls_booked"]["delta"] == 2

        assert m["proposals_out"]["current"] == 1
        assert m["proposals_out"]["prior"] == 1
        assert m["proposals_out"]["delta"] == 0

        assert m["closed"]["current"] == 1
        assert m["closed"]["prior"] == 1
        assert m["closed"]["delta"] == 0

        # 500_000 (50% of 1_000_000 deposit) + 300_000 (balance) = 800_000
        assert m["cash_collected_cents"]["current"] == 800_000
        # 50% of 2_000_000 deposit
        assert m["cash_collected_cents"]["prior"] == 1_000_000
        assert m["cash_collected_cents"]["delta"] == -200_000
        assert m["cash_collected_cents"]["pct"] == round((-200_000 / 1_000_000) * 100, 1)

        assert m["weeks_booked"]["current"] == 3  # week A, B, D
        assert m["weeks_booked"]["prior"] == 3    # week A, B, C

        assert m["app_trials"]["current"] == 3  # u1, u4, u5
        assert m["app_trials"]["prior"] == 1     # u2

        # current MRR: u1 (physician $149) + u2 (applicant $49) + u3 (program->program_seat $199) + u5 (unmapped, $0)
        assert m["mrr_cents"]["current"] == 14900 + 4900 + 19900 + 0
        # prior MRR: only users that existed as of PRIOR_END: u2 + u3 (u1, u5 too new; u4 not active anyway)
        assert m["mrr_cents"]["prior"] == 4900 + 19900
        expected_delta = (14900 + 4900 + 19900) - (4900 + 19900)
        assert m["mrr_cents"]["delta"] == expected_delta
        assert m["mrr_cents"]["pct"] == round((expected_delta / (4900 + 19900)) * 100, 1)


def test_build_digest_window_boundaries_are_half_open(app):
    """[start, end) on both sides: a row exactly at the shared boundary
    (CURR_START == PRIOR_END) belongs to the current window only; a row at
    CURR_END belongs to neither (it's the "as_of" instant itself)."""
    with app.app_context():
        lead = _lead()
        db.session.add(Message(lead_id=lead.id, direction="outbound", sent_at=CURR_START))
        db.session.add(Message(lead_id=lead.id, direction="outbound", sent_at=CURR_END))
        db.session.commit()

        digest = build_digest(as_of=AS_OF)
        # Only the CURR_START row counts, and it counts for "current", not "prior".
        assert digest["metrics"]["touches_sent"]["current"] == 1
        assert digest["metrics"]["touches_sent"]["prior"] == 0


def test_build_digest_empty_database_returns_zeros(app):
    with app.app_context():
        digest = build_digest(as_of=AS_OF)
        for key, metric in digest["metrics"].items():
            assert metric["current"] == 0, key
            assert metric["prior"] == 0, key
            assert metric["delta"] == 0, key


def test_send_digest_requires_founder_email(app):
    with app.app_context():
        app.config["FOUNDER_EMAIL"] = ""
        with patch("ops.digest.gmail_client.send_email") as mock_send:
            try:
                send_digest(as_of=AS_OF)
                assert False, "expected RuntimeError"
            except RuntimeError as e:
                assert "FOUNDER_EMAIL" in str(e)
        mock_send.assert_not_called()


def test_send_digest_sends_email_via_gmail_client(app):
    with app.app_context():
        app.config["FOUNDER_EMAIL"] = "founder@example.com"
        with patch("ops.digest.gmail_client.send_email", return_value={"message_id": "m1", "thread_id": "t1"}) as mock_send:
            result = send_digest(as_of=AS_OF)

        assert result == {"message_id": "m1", "thread_id": "t1"}
        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        assert kwargs["to_email"] == "founder@example.com"
        assert "Weekly Digest" in kwargs["subject"]
        assert "<html" not in kwargs["html_body"]  # digest.py hands gmail_client a body fragment, not a full doc
        assert "RPSAS" in kwargs["subject"] or "RPSAS" in kwargs["html_body"]
