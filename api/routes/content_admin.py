"""Approval queue for the content engine (Phase 3).

Nothing produced by content/ingest.py ever leaves status="draft" except
through this blueprint. content/schedule.py refuses to push anything that
isn't status="approved" (see its own docstring), so this is the one and
only gate between an AI-drafted post and anything going out publicly.

Standalone blueprint, not yet registered in api/app.py — see
content/INTEGRATION.md for the one-line registration snippet (kept out of
app.py itself to avoid clashing with other in-flight work there).
"""

from flask import Blueprint, redirect, render_template, request, url_for, flash

from extensions import db
from models import ContentItem
from routes.admin import admin_required

bp = Blueprint("content_admin", __name__, url_prefix="/admin/content")

STATUSES = ("draft", "approved", "scheduled", "published", "rejected")


@bp.get("")
@admin_required
def queue():
    status_filter = request.args.get("status") or None
    items_query = ContentItem.query.order_by(ContentItem.created.desc())
    if status_filter:
        items_query = items_query.filter_by(status=status_filter)
    items = items_query.limit(500).all()

    # NOTE: the per-batch dict below deliberately does NOT use the key "items"
    # -- Jinja resolves `batch.items` to dict.items() (the bound method) before
    # falling back to key lookup, since dicts have a real `items` attribute.
    # "content_items" avoids that collision.
    batches = {}
    for item in items:
        meta = item.item_metadata or {}
        batch_id = meta.get("batch_id") or "unbatched"
        batch = batches.setdefault(
            batch_id, {"batch_id": batch_id, "source_video": meta.get("source_video") or "—", "content_items": []}
        )
        if batch["source_video"] == "—" and meta.get("source_video"):
            batch["source_video"] = meta["source_video"]
        batch["content_items"].append(item)

    batch_list = sorted(
        batches.values(),
        key=lambda b: max(i.created for i in b["content_items"] if i.created is not None),
        reverse=True,
    )

    counts = {s: ContentItem.query.filter_by(status=s).count() for s in STATUSES}

    return render_template(
        "admin/content.html", batches=batch_list, counts=counts, statuses=STATUSES, status_filter=status_filter
    )


@bp.post("/<int:item_id>/approve")
@admin_required
def approve(item_id):
    item = ContentItem.query.get_or_404(item_id)
    if item.status != "draft":
        flash(f"ContentItem {item.id} is not in draft (status={item.status}); not approved.", "error")
        return redirect(url_for("content_admin.queue"))
    item.status = "approved"
    db.session.commit()
    flash(f"Approved #{item.id} ({item.type}).", "success")
    return redirect(url_for("content_admin.queue"))


@bp.post("/approve-all")
@admin_required
def approve_all():
    batch_id = request.form.get("batch_id")
    if not batch_id:
        flash("Missing batch_id.", "error")
        return redirect(url_for("content_admin.queue"))

    drafts = ContentItem.query.filter_by(status="draft").all()
    matched = [i for i in drafts if (i.item_metadata or {}).get("batch_id") == batch_id]
    for item in matched:
        item.status = "approved"
    db.session.commit()

    flash(f"Approved {len(matched)} item(s) in batch {batch_id}.", "success")
    return redirect(url_for("content_admin.queue"))


@bp.post("/<int:item_id>/reject")
@admin_required
def reject(item_id):
    item = ContentItem.query.get_or_404(item_id)
    item.status = "rejected"
    db.session.commit()
    flash(f"Rejected #{item.id} ({item.type}).", "success")
    return redirect(url_for("content_admin.queue"))
