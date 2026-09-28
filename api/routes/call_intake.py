"""Phase 6 call-to-proposal admin flow — paste a discovery-call transcript,
Claude extracts package/date/participants/special terms, and the existing
proposal PDF + e-sign + Stripe deposit pipeline runs automatically. Nothing
goes out until the founder approves it (or the 2-hour auto-approve sweep
fires — see `flask auto-approve-proposals` in api/app.py). See
api/INTEGRATION.md for the one-line registration this needs in api/app.py
(not edited here — another engineer is actively working in/around that
file)."""

import io

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for

from extensions import db
from models import PACKAGES, Deal, Lead
from routes.admin import admin_required
from services import call_to_proposal

bp = Blueprint("call_intake", __name__, url_prefix="/admin/calls")


@bp.get("/new")
@admin_required
def new():
    leads = Lead.query.order_by(Lead.created.desc()).limit(200).all()
    return render_template("admin/call_intake.html", leads=leads)


@bp.post("")
@admin_required
def create():
    lead_id = request.form.get("lead_id", type=int)
    deal_id = request.form.get("deal_id", type=int)
    transcript_text = (request.form.get("transcript") or "").strip()

    lead = Lead.query.get_or_404(lead_id) if lead_id else None
    if not lead or not transcript_text:
        flash("A lead and a transcript are both required.", "error")
        return redirect(url_for("call_intake.new"))

    if deal_id:
        deal = Deal.query.filter_by(id=deal_id, lead_id=lead.id).first()
        if not deal:
            flash("That deal doesn't belong to the selected lead.", "error")
            return redirect(url_for("call_intake.new"))
    else:
        # Placeholder values -- generate_proposal_from_call() overwrites
        # package/amount_cents once extraction runs.
        deal = Deal(lead_id=lead.id, package="tbd", amount_cents=0, balance_due_cents=0, stage="discovery")
        db.session.add(deal)
        db.session.flush()

    call_to_proposal.generate_proposal_from_call(deal, transcript_text)

    flash("Proposal generated from the call — review before it goes out.", "success")
    return redirect(url_for("call_intake.review", deal_id=deal.id))


@bp.get("/<int:deal_id>/review")
@admin_required
def review(deal_id):
    deal = Deal.query.get_or_404(deal_id)
    if not deal.proposal_context:
        abort(404)
    package_label = PACKAGES.get(deal.package, {}).get("label", deal.package)
    return render_template("admin/call_review.html", deal=deal, lead=deal.lead, package_label=package_label)


@bp.get("/<int:deal_id>/pdf")
@admin_required
def preview_pdf(deal_id):
    deal = Deal.query.get_or_404(deal_id)
    if not deal.proposal_pdf_data:
        abort(404)
    return send_file(
        io.BytesIO(deal.proposal_pdf_data), mimetype="application/pdf",
        download_name=f"rpsas-proposal-{deal.id}.pdf",
    )


@bp.post("/<int:deal_id>/approve")
@admin_required
def approve(deal_id):
    deal = Deal.query.get_or_404(deal_id)
    if not call_to_proposal.approve_proposal(deal):
        flash("This proposal isn't pending review.", "error")
        return redirect(url_for("call_intake.review", deal_id=deal.id))

    flash("Proposal approved and activated — the sign link and deposit link are now live.", "success")
    return redirect(url_for("admin.deal_detail", deal_id=deal.id))
