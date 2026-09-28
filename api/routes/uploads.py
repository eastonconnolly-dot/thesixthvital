"""Public, token-based baseline-video upload (Phase 6). Local disk storage
under UPLOAD_STORAGE_DIR -- a real S3-compatible bucket is a documented
future swap (see api/INTEGRATION.md), out of scope here. Standalone
blueprint, not yet registered in api/app.py -- see api/INTEGRATION.md.

Security notes (this is a public, unauthenticated endpoint):
  - The stored filename is always the UploadLink's own token + a validated
    extension -- the client-provided filename is never used for the path.
  - Extension allowlist (mp4/mov/webm) and a byte-size cap
    (UPLOAD_MAX_BYTES) are both enforced server-side before anything is
    written to disk.
"""

import os

from flask import Blueprint, abort, current_app, render_template, request

from extensions import db
from models import UploadLink, utcnow

bp = Blueprint("uploads", __name__)


def _ext_ok(filename):
    if not filename or "." not in filename:
        return None
    ext = filename.rsplit(".", 1)[1].lower()
    if ext in current_app.config["UPLOAD_ALLOWED_EXTENSIONS"]:
        return ext
    return None


@bp.get("/upload/<token>")
def upload_form(token):
    link = UploadLink.query.filter_by(token=token).first()
    if not link:
        abort(404)
    return render_template(
        "uploads/form.html", link=link, already_uploaded=link.uploaded_at is not None,
        allowed_extensions=sorted(current_app.config["UPLOAD_ALLOWED_EXTENSIONS"]),
        max_mb=current_app.config["UPLOAD_MAX_BYTES"] // (1024 * 1024),
    )


@bp.post("/upload/<token>")
def upload_submit(token):
    link = UploadLink.query.filter_by(token=token).first()
    if not link:
        abort(404)

    allowed = sorted(current_app.config["UPLOAD_ALLOWED_EXTENSIONS"])
    max_mb = current_app.config["UPLOAD_MAX_BYTES"] // (1024 * 1024)

    if link.uploaded_at is not None:
        return render_template(
            "uploads/form.html", link=link, already_uploaded=True,
            allowed_extensions=allowed, max_mb=max_mb,
            error="A video has already been uploaded for this link.",
        ), 409

    file = request.files.get("video")
    if not file or not file.filename:
        return render_template(
            "uploads/form.html", link=link, already_uploaded=False,
            allowed_extensions=allowed, max_mb=max_mb, error="Please choose a video file.",
        ), 400

    ext = _ext_ok(file.filename)
    if not ext:
        return render_template(
            "uploads/form.html", link=link, already_uploaded=False,
            allowed_extensions=allowed, max_mb=max_mb,
            error=f"File type not allowed. Accepted: {', '.join(allowed)}.",
        ), 400

    max_bytes = current_app.config["UPLOAD_MAX_BYTES"]
    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)
    if size > max_bytes:
        return render_template(
            "uploads/form.html", link=link, already_uploaded=False,
            allowed_extensions=allowed, max_mb=max_mb,
            error=f"File too large. Max {max_mb}MB.",
        ), 400
    if size == 0:
        return render_template(
            "uploads/form.html", link=link, already_uploaded=False,
            allowed_extensions=allowed, max_mb=max_mb, error="That file is empty.",
        ), 400

    storage_dir = current_app.config["UPLOAD_STORAGE_DIR"]
    os.makedirs(storage_dir, exist_ok=True)
    # Storage path is derived entirely from the link's own token (never the
    # client-supplied filename) -- token is a secrets.token_urlsafe value,
    # so it's already path-safe.
    dest_path = os.path.join(storage_dir, f"{link.token}.{ext}")
    file.save(dest_path)

    link.uploaded_at = utcnow()
    link.file_path = dest_path
    db.session.commit()

    return render_template(
        "uploads/form.html", link=link, already_uploaded=True,
        allowed_extensions=allowed, max_mb=max_mb, just_uploaded=True,
    )
