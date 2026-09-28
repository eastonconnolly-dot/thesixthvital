import functools
from datetime import datetime

from flask import (
    Blueprint, current_app, flash, redirect, render_template, request, session, url_for,
)

from extensions import db
from models import PACKAGES, Deal, Lead
from services import esign, hub_sync, stripe_client
from services.onboarding import ensure_calendar_holds
from services.pdf.proposal import render_proposal_pdf

bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if current_app.config["ADMIN_PASSWORD"] and password == current_app.config["ADMIN_PASSWORD"]:
            session["is_admin"] = True
            return redirect(request.args.get("next") or url_for("admin.dashboard"))
        flash("Incorrect password.", "error")
    return render_template("admin/login.html")


@bp.get("/logout")
def logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin.login"))


@bp.get("/")
@admin_required
def dashboard():
    leads = Lead.query.order_by(Lead.created.desc()).limit(100).all()
    deals = Deal.query.order_by(Deal.created.desc()).limit(100).all()
    return render_template("admin/dashboard.html", leads=leads, deals=deals, packages=PACKAGES)


@bp.post("/leads/<int:lead_id>/deals")
@admin_required
def create_deal(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    package_key = request.form.get("package")
    package = PACKAGES.get(package_key)
    if not package:
        flash("Unknown package.", "error")
        return redirect(url_for("admin.dashboard"))

    delivery_date = request.form.get("delivery_date") or None
    deal = Deal(
        lead_id=lead.id,
        package=package_key,
        amount_cents=package["amount_cents"],
        balance_due_cents=package["amount_cents"],
        delivery_date=datetime.strptime(delivery_date, "%Y-%m-%d").date() if delivery_date else None,
        stage="discovery",
    )
    db.session.add(deal)
    db.session.commit()

    # Best-effort Hub sync (see HUB_INTEGRATION.md) — a real deal is the
    # natural "this lead is worth having in the Hub CRM too" moment. No-ops
    # cleanly until the Hub side is provisioned and HUB_API_KEY is set.
    hub_sync.push_lead(lead)
    hub_sync.push_deal_update(deal)

    return redirect(url_for("admin.deal_detail", deal_id=deal.id))


@bp.get("/deals/<int:deal_id>")
@admin_required
def deal_detail(deal_id):
    deal = Deal.query.get_or_404(deal_id)
    package_label = PACKAGES.get(deal.package, {}).get("label", deal.package)
    return render_template("admin/deal_detail.html", deal=deal, lead=deal.lead, package_label=package_label)


@bp.post("/deals/<int:deal_id>/delivery-date")
@admin_required
def set_delivery_date(deal_id):
    """Sets or corrects a deal's delivery_date after it was created --
    covers a call-to-proposal extraction that didn't find a date in the
    transcript, and self-serve package checkouts (routes/webhooks.py's
    _handle_package_checkout), which have no discovery call at all and so
    never get a delivery_date automatically. If onboarding already ran
    (routes/esign.py, or the package-checkout path), the calendar holds it
    would have booked were skipped at the time for lack of a date --
    ensure_calendar_holds() books them now instead of leaving them skipped
    forever."""
    deal = Deal.query.get_or_404(deal_id)
    raw = request.form.get("delivery_date") or None
    if not raw:
        flash("A delivery date is required.", "error")
        return redirect(url_for("admin.deal_detail", deal_id=deal.id))

    try:
        deal.delivery_date = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        flash("Delivery date must be YYYY-MM-DD.", "error")
        return redirect(url_for("admin.deal_detail", deal_id=deal.id))

    db.session.commit()
    booked = ensure_calendar_holds(deal)
    if booked:
        flash(f"Delivery date saved and calendar holds booked ({', '.join(booked)}).", "success")
    else:
        flash("Delivery date saved.", "success")
    return redirect(url_for("admin.deal_detail", deal_id=deal.id))


@bp.post("/deals/<int:deal_id>/proposal")
@admin_required
def generate_proposal(deal_id):
    deal = Deal.query.get_or_404(deal_id)
    lead = deal.lead
    package_label = PACKAGES.get(deal.package, {}).get("label", deal.package)
    deliverables = [
        f"{package_label} intensive",
        "Baseline and final encounter scoring on the 5-dimension rubric",
        "A scorecard report within one week of delivery",
    ]

    success_url = f"{current_app.config['SITE_BASE_URL']}/apply.html?deposit=paid"
    cancel_url = f"{current_app.config['SITE_BASE_URL']}/apply.html?deposit=cancelled"
    checkout = stripe_client.create_deposit_checkout_session(deal, package_label, success_url, cancel_url)
    deal.deposit_stripe_session_id = checkout["id"]

    deal.proposal_context = {
        "lead_name": lead.name,
        "lead_org": lead.org,
        "package_label": package_label,
        "deliverables": deliverables,
        "deposit_link": checkout["url"],
        "delivery_date": deal.delivery_date.isoformat() if deal.delivery_date else None,
        "mailing_address": current_app.config["COMPANY_MAILING_ADDRESS"],
    }

    pdf_buf = render_proposal_pdf(
        deal={"amount_cents": deal.amount_cents, "delivery_date": deal.proposal_context["delivery_date"]},
        lead={"name": lead.name, "org": lead.org},
        package_label=package_label,
        deliverables=deliverables,
        deposit_link=checkout["url"],
        mailing_address=current_app.config["COMPANY_MAILING_ADDRESS"],
    )
    deal.proposal_pdf_data = pdf_buf.getvalue()

    sig_request, sign_url = esign.create_signature_request(deal, signer_name=lead.name, signer_email=lead.email)
    deal.stage = "proposal_sent"
    db.session.commit()

    flash(f"Proposal generated. Sign link: {sign_url}", "success")
    return redirect(url_for("admin.deal_detail", deal_id=deal.id))
