import io
import os

from extensions import db
from models import Deal, Lead, UploadLink


def _make_link(app):
    lead = Lead(name="Dana Ortiz", email="dana@example.com", track="physician")
    db.session.add(lead)
    db.session.flush()
    deal = Deal(lead_id=lead.id, package="physician_private", amount_cents=1250000, balance_due_cents=1250000)
    db.session.add(deal)
    db.session.flush()
    link = UploadLink(deal_id=deal.id, participant_name="Dana Ortiz", participant_email="dana@example.com", token="upload-tok-1")
    db.session.add(link)
    db.session.commit()
    return link


def test_upload_form_renders_for_valid_token(app, client):
    with app.app_context():
        _make_link(app)
    resp = client.get("/upload/upload-tok-1")
    assert resp.status_code == 200
    assert b"Dana Ortiz" in resp.data


def test_upload_form_404_for_unknown_token(client):
    resp = client.get("/upload/not-a-real-token")
    assert resp.status_code == 404


def test_upload_accepts_valid_video(app, client):
    with app.app_context():
        _make_link(app)

    data = {"video": (io.BytesIO(b"fake mp4 bytes" * 10), "baseline.mp4")}
    resp = client.post("/upload/upload-tok-1", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200
    assert b"uploaded successfully" in resp.data

    with app.app_context():
        link = UploadLink.query.filter_by(token="upload-tok-1").first()
        assert link.uploaded_at is not None
        assert link.file_path is not None
        assert os.path.exists(link.file_path)
        # storage path is derived from the token, never the client filename
        assert "baseline" not in os.path.basename(link.file_path)
        assert link.file_path.endswith(".mp4")


def test_upload_rejects_disallowed_extension(app, client):
    with app.app_context():
        _make_link(app)
    data = {"video": (io.BytesIO(b"not a video"), "baseline.exe")}
    resp = client.post("/upload/upload-tok-1", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert b"not allowed" in resp.data

    with app.app_context():
        link = UploadLink.query.filter_by(token="upload-tok-1").first()
        assert link.uploaded_at is None


def test_upload_rejects_oversized_file(app, client):
    with app.app_context():
        _make_link(app)
    app.config["UPLOAD_MAX_BYTES"] = 10  # tiny cap for the test
    try:
        data = {"video": (io.BytesIO(b"x" * 1000), "baseline.mp4")}
        resp = client.post("/upload/upload-tok-1", data=data, content_type="multipart/form-data")
        assert resp.status_code == 400
        assert b"too large" in resp.data
    finally:
        app.config["UPLOAD_MAX_BYTES"] = 500 * 1024 * 1024


def test_upload_rejects_missing_file(app, client):
    with app.app_context():
        _make_link(app)
    resp = client.post("/upload/upload-tok-1", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_upload_rejects_second_attempt(app, client):
    with app.app_context():
        _make_link(app)
    data1 = {"video": (io.BytesIO(b"first upload bytes"), "a.mp4")}
    resp1 = client.post("/upload/upload-tok-1", data=data1, content_type="multipart/form-data")
    assert resp1.status_code == 200

    data2 = {"video": (io.BytesIO(b"second upload bytes"), "b.mov")}
    resp2 = client.post("/upload/upload-tok-1", data=data2, content_type="multipart/form-data")
    assert resp2.status_code == 409


def test_upload_404_post_for_unknown_token(client):
    data = {"video": (io.BytesIO(b"x"), "a.mp4")}
    resp = client.post("/upload/not-a-real-token", data=data, content_type="multipart/form-data")
    assert resp.status_code == 404
