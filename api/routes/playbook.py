"""Admin view for the Phase 6 sales call playbook (see
services/call_playbook.py). Standalone blueprint, not yet registered in
api/app.py's own blueprint list -- see this repo's top-level
INTEGRATION.md for the one-line registration snippet (kept out of app.py
itself the same way content_admin.py's registration was, to avoid
clashing with other in-flight work there; api/tests/conftest.py registers
it directly for the test suite in the meantime).
"""

from flask import Blueprint, flash, redirect, render_template, url_for

from routes.admin import admin_required
from services import call_playbook

bp = Blueprint("playbook", __name__, url_prefix="/admin/playbook")


@bp.get("")
@admin_required
def show():
    playbook = call_playbook.latest_playbook()
    available, transcript_count = call_playbook.transcript_status()
    return render_template(
        "admin/playbook.html",
        playbook=playbook,
        available=available,
        transcript_count=transcript_count,
        min_required=call_playbook.MIN_TRANSCRIPTS_FOR_PLAYBOOK,
    )


@bp.post("/regenerate")
@admin_required
def regenerate():
    result = call_playbook.generate_playbook()
    if not result["available"]:
        flash("No call-transcript source is configured yet — nothing to generate from.", "error")
    elif result["playbook"] is None:
        flash(f"Not enough transcribed calls yet ({result['reason']}).", "error")
    else:
        flash(f"Playbook regenerated from {result['transcript_count']} calls.", "success")
    return redirect(url_for("playbook.show"))
