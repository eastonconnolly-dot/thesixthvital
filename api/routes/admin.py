import functools
from datetime import datetime

from flask import (
    Blueprint, current_app, flash, redirect, render_template, request, session, url_for,
)

from extensions import db
from models import PACKAGES, Deal, Lead
from services import signwell_client, stripe_client
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
    return redirect(url_for("admin.deal_detail", deal_id=deal.id))


@bp.get("/deals/<int:deal_id>")
@admin_required
def deal_detail(deal_id):
    deal = Deal.query.get_or_404(deal_id)
    package_label = PACKAGES.get(deal.package, {}).get("label", deal.package)
    return render_template("admin/deal_detail.html", deal=deal, lead=deal.lead, package_label=package_label)


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

    pdf_buf = render_proposal_pdf(
        deal={"amount_cents": deal.amount_cents, "delivery_date": deal.delivery_date.isoformat() if deal.delivery_date else None},
        lead={"name": lead.name, "org": lead.org},
        package_label=package_label,
        deliverables=deliverables,
        deposit_link=checkout["url"],
        mailing_address=current_app.config["COMPANY_MAILING_ADDRESS"],
    )

    envelope = signwell_client.create_envelope(
        pdf_bytes=pdf_buf.getvalue(),
        filename=f"proposal-{deal.id}.pdf",
        signer_name=lead.name,
        signer_email=lead.email,
        deal_id=deal.id,
    )
    deal.signwell_envelope_id = envelope["envelope_id"]
    deal.stage = "proposal_sent"
    db.session.commit()

    flash(
        f"Proposal generated{' (SignWell test mode)' if envelope['test_mode'] else ''}. "
        f"Sign link: {envelope.get('sign_url')}",
        "success",
    )
    return redirect(url_for("admin.deal_detail", deal_id=deal.id))
