"""Small helpers shared by services/*.py's outbound-email code (onboarding.py,
delivery.py, qualifier_chat.py, print_vendor.py). Extracted because each had
grown its own copy of the same three snippets with slightly different
edge-case behavior -- including a latent bug in delivery.py's version of
first_name(): it fell back to `None` instead of a greeting fallback when a
participant's name was missing, which would have rendered as a literal
"Hi None," in the outgoing email.

Deliberately scoped to api/services/ only, not shared with outreach/ or
content/ -- those are separate top-level packages with their own
`util.add_api_to_path()` import setup, and onboarding.py already documents
why Phase 6 keeps its own automation decoupled from outreach/engine/'s
machinery rather than reusing it.
"""

import secrets

from flask import current_app


def make_token(nbytes=24):
    return secrets.token_urlsafe(nbytes)


def unsubscribe_url(lead_id):
    """`lead_id` is a bare id (pass `lead.id`, or None for a recipient with
    no Lead row yet), not a Lead object -- every call site already has the
    id in hand and passing the bare value avoids each caller needing to
    know whether the object could be None."""
    if not lead_id:
        return current_app.config["API_BASE_URL"]
    return f"{current_app.config['API_BASE_URL']}/unsubscribe?lead_id={lead_id}"


def first_name(name, fallback="there"):
    return (name or "").split(" ")[0] or fallback
