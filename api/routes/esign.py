import base64
import binascii
import io

from flask import Blueprint, abort, current_app, jsonify, render_template, request, send_file

from extensions import db
from services import esign
from services.pdf.proposal import render_proposal_pdf

bp = Blueprint("esign", __name__)


def _get_request_or_404(token):
    sig_request = esign.get_by_token(token)
    if not sig_request:
        abort(404)
    return sig_request


@bp.get("/sign/<token>")
def sign_page(token):
    sig_request = _get_request_or_404(token)
    deal = sig_request.deal
    context = deal.proposal_context or {}
    return render_template(
        "sign/pad.html",
        sig_request=sig_request,
        deal=deal,
        context=context,
    )


@bp.get("/sign/<token>/document.pdf")
def sign_document(token):
    sig_request = _get_request_or_404(token)
    deal = sig_request.deal
    data = deal.signed_pdf_data if sig_request.status == "signed" else deal.proposal_pdf_data
    if not data:
        abort(404)
    return send_file(
        io.BytesIO(data), mimetype="application/pdf",
        download_name=f"rpsas-proposal-{deal.id}.pdf",
    )


@bp.post("/sign/<token>")
def submit_signature(token):
    sig_request = _get_request_or_404(token)
    if sig_request.status != "pending":
        return jsonify({"error": f"already {sig_request.status}"}), 409

    data = request.get_json(silent=True) or {}
    typed_name = (data.get("typed_name") or "").strip()
    signature_data_url = data.get("signature_png_base64") or ""
    agreed = bool(data.get("agreed"))

    if not typed_name or not signature_data_url or not agreed:
        return jsonify({"error": "typed_name, signature_png_base64, and agreed are all required"}), 400

    try:
        header, b64 = signature_data_url.split(",", 1) if "," in signature_data_url else ("", signature_data_url)
        png_bytes = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        return jsonify({"error": "signature_png_base64 must be a valid base64-encoded PNG"}), 400

    if not png_bytes.startswith(b"\x89PNG"):
        return jsonify({"error": "signature must be a PNG image"}), 400

    ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    esign.record_signature(sig_request, typed_name, png_bytes, ip)

    deal = sig_request.deal
    context = deal.proposal_context or {}
    signed_at_label = sig_request.signed_at.strftime("%B %d, %Y")
    pdf_buf = render_proposal_pdf(
        deal={"amount_cents": deal.amount_cents, "delivery_date": context.get("delivery_date")},
        lead={"name": context.get("lead_name"), "org": context.get("lead_org")},
        package_label=context.get("package_label"),
        deliverables=context.get("deliverables", []),
        deposit_link=context.get("deposit_link"),
        mailing_address=context.get("mailing_address"),
        signature={
            "typed_name": typed_name,
            "signer_name": sig_request.signer_name,
            "signed_at": signed_at_label,
            "png_bytes": png_bytes,
        },
    )
    deal.signed_pdf_data = pdf_buf.getvalue()
    db.session.commit()

    # Phase 6 onboarding kickoff — best-effort. A trainee's signature must
    # never fail because a downstream welcome email / calendar hold /
    # badge order hit an unconfigured integration; log and move on.
    try:
        from services.onboarding import trigger_onboarding
        trigger_onboarding(deal)
    except Exception as e:
        current_app.logger.warning(f"onboarding trigger failed for deal {deal.id}: {e}")

    return jsonify({
        "status": "signed",
        "deposit_link": context.get("deposit_link"),
        "document_url": f"/sign/{token}/document.pdf",
    })
