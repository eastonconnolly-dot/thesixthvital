"""Magic-link auth for RPSAS Practice — durable token + short expiry,
following the shape of the Hub's own hub/homeowner_portal.py pattern (a
token-based customer-facing login) rather than username/password."""

import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app

from extensions import db
from models import Lead, MagicLinkToken, PracticeUser, _aware

TOKEN_TTL_MINUTES = 15


def request_login(email, track=None, name=None):
    """Creates a magic-link token for this email. If no PracticeUser exists
    yet, `track` is required to create one (and a matching Lead). Returns
    the sign-in URL."""
    email = email.strip().lower()
    user = PracticeUser.query.filter_by(email=email).first()
    if not user:
        if not track:
            raise ValueError("track is required for a new signup")
        lead = Lead(name=name or email, email=email, track=track, source="practice_app")
        db.session.add(lead)
        db.session.flush()
        user = PracticeUser(
            email=email, name=name, track=track, lead_id=lead.id,
            subscription_status="trialing",
            trial_ends_at=datetime.now(timezone.utc) + timedelta(days=current_app.config["PRACTICE_TRIAL_DAYS"]),
        )
        db.session.add(user)

    token = MagicLinkToken(
        token=secrets.token_urlsafe(32), email=email,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=TOKEN_TTL_MINUTES),
    )
    db.session.add(token)
    db.session.commit()

    return f"{current_app.config['API_BASE_URL']}/practice/auth/{token.token}", user


def verify_and_consume(token_str):
    """Returns the PracticeUser if the token is valid and unused, else None."""
    token = MagicLinkToken.query.filter_by(token=token_str).first()
    if not token or token.used_at is not None:
        return None
    if _aware(token.expires_at) < datetime.now(timezone.utc):
        return None
    token.used_at = datetime.now(timezone.utc)
    db.session.commit()
    return PracticeUser.query.filter_by(email=token.email).first()
