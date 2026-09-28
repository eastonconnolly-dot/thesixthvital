"""Native e-signature: a secret sign-link token plus canvas-captured
signature, following the shape of the Hub's own hub/esign.py rather than
integrating a third-party vendor (SignWell, DocuSign, etc.) — no per-envelope
fee, no external account, and the founder already trusts this pattern
because it's what the Hub itself runs in production."""

import secrets
from datetime import datetime, timezone

from flask import current_app

from extensions import db
from models import Deal, SignatureRequest


def _new_token():
    return secrets.token_urlsafe(32)


def create_signature_request(deal, signer_name, signer_email):
    request = SignatureRequest(
        deal_id=deal.id,
        token=_new_token(),
        signer_name=signer_name,
        signer_email=signer_email,
        status="pending",
    )
    db.session.add(request)
    db.session.commit()
    sign_url = f"{current_app.config['API_BASE_URL']}/sign/{request.token}"
    return request, sign_url


def get_by_token(token):
    return SignatureRequest.query.filter_by(token=token).first()


def record_signature(request, typed_name, signature_png_bytes, signer_ip):
    if request.status != "pending":
        raise ValueError(f"signature request is already {request.status}")

    request.typed_name = typed_name
    request.signature_png = signature_png_bytes
    request.signer_ip = signer_ip
    request.status = "signed"
    request.signed_at = datetime.now(timezone.utc)

    deal = db.session.get(Deal, request.deal_id)
    deal.signed_at = request.signed_at

    db.session.commit()
    return request
