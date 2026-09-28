"""closers.py — Phase 6, "sales handoff readiness": commission computation
for delegated closers.

Deal.stage flips to "deposit_paid" inside api/routes/webhooks.py (the
Stripe webhook handler), which is out of scope for this change (another
engineer is actively working there) -- so rather than edit that file, the
commission sync is a small, idempotent, call-it-whenever-you-like function.
It's wired into every place a closer's deals are read or written from
admin (assign-closer, the closer calendar view), so a deal's commission
gets computed the first time any of those paths notices the deal has
reached a real-close stage. See INTEGRATION.md for the one-line addition
that would make webhooks.py trigger this the instant the stage flips,
once that file is no longer in flight.
"""

from extensions import db
from models import Closer, utcnow

# Stages at which a deal represents a genuine close -- mirrors
# ops/digest.py's own "closed" metric (stage in (deposit_paid, delivered)).
COMMISSION_TRIGGER_STAGES = ("deposit_paid", "delivered")


def sync_commission(deal):
    """Sets deal.commission_cents exactly once, and only once the deal has
    actually reached a stage that represents a real close. Never
    speculative: a deal that hasn't reached deposit_paid/delivered yet is
    left untouched no matter how many times this is called. Idempotent:
    once commission_cents is set, this is a no-op for that deal (so a
    closer's commission_rate changing later doesn't retroactively rewrite
    a commission already earned). No-ops quietly if the deal has no
    closer_id (founder-owned) or the referenced Closer row is missing.

    Commits if it makes a change. Returns the deal either way.
    """
    if not deal.closer_id or deal.commission_cents is not None:
        return deal
    if deal.stage not in COMMISSION_TRIGGER_STAGES:
        return deal

    closer = db.session.get(Closer, deal.closer_id)
    if not closer:
        return deal

    deal.commission_cents = round(deal.amount_cents * closer.commission_rate)
    db.session.commit()
    return deal


def sync_commissions_for_closer(closer):
    for deal in closer.deals:
        sync_commission(deal)
