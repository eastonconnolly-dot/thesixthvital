"""Admin views for Phase 6's delegated-closer role: create closers, assign
a closer to a deal, and a per-closer "calendar" (their assigned deals'
delivery dates -- see this repo's top-level INTEGRATION.md for why that's
a legitimate calendar for this scope rather than a real calendar
integration). Standalone blueprint, not yet registered in api/app.py --
see INTEGRATION.md for the one-line registration snippet.
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from extensions import db
from models import Closer, Deal
from routes.admin import admin_required
from services.closers import sync_commission, sync_commissions_for_closer

bp = Blueprint("closers", __name__, url_prefix="/admin")


@bp.get("/closers")
@admin_required
def list_closers():
    closers = Closer.query.order_by(Closer.created.desc()).all()
    # Deals not yet assigned to a closer -- the assign-closer form on this
    # page covers this without needing to touch admin/deal_detail.html
    # (out of scope here; another engineer is actively working around it).
    unassigned_deals = (
        Deal.query.filter(Deal.closer_id.is_(None)).order_by(Deal.created.desc()).limit(50).all()
    )
    return render_template("admin/closers.html", closers=closers, unassigned_deals=unassigned_deals)


@bp.post("/closers")
@admin_required
def create_closer():
    name = (request.form.get("name") or "").strip()
    email = (request.form.get("email") or "").strip()
    rate_raw = request.form.get("commission_rate") or ""

    if not name or not email:
        flash("Name and email are required.", "error")
        return redirect(url_for("closers.list_closers"))

    try:
        commission_rate = float(rate_raw)
    except ValueError:
        flash("Commission rate must be a number (e.g. 0.10 for 10%).", "error")
        return redirect(url_for("closers.list_closers"))

    if Closer.query.filter_by(email=email).first():
        flash(f"A closer with email {email} already exists.", "error")
        return redirect(url_for("closers.list_closers"))

    closer = Closer(name=name, email=email, commission_rate=commission_rate)
    db.session.add(closer)
    db.session.commit()
    flash(f"Added closer {closer.name} ({closer.commission_rate:.0%} commission).", "success")
    return redirect(url_for("closers.list_closers"))


@bp.post("/deals/<int:deal_id>/assign-closer")
@admin_required
def assign_closer(deal_id):
    deal = Deal.query.get_or_404(deal_id)
    closer_id = request.form.get("closer_id") or None

    if closer_id:
        closer = Closer.query.get_or_404(int(closer_id))
        deal.closer_id = closer.id
        db.session.commit()
        # In case this deal already reached a real-close stage before a
        # closer was assigned to it -- sync_commission() is a no-op if not.
        sync_commission(deal)
        flash(f"Assigned {closer.name} to deal #{deal.id}.", "success")
    else:
        deal.closer_id = None
        db.session.commit()
        flash(f"Deal #{deal.id} is now founder-owned.", "success")

    return redirect(url_for("closers.list_closers"))


@bp.get("/closers/<int:closer_id>/calendar")
@admin_required
def calendar(closer_id):
    closer = Closer.query.get_or_404(closer_id)
    # Deposit-paid/delivered deals may have reached that stage since we last
    # looked -- catch the commission up before rendering.
    sync_commissions_for_closer(closer)
    deals = sorted(closer.deals, key=lambda d: (d.delivery_date is None, d.delivery_date))
    return render_template("admin/closer_calendar.html", closer=closer, deals=deals)
