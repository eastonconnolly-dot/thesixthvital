from extensions import db
from models import ContentItem


def _login(client):
    return client.post("/admin/login", data={"password": "test-admin-password"}, follow_redirects=True)


def _make_item(batch_id="batch1", type_="linkedin_post", status="draft", **meta_overrides):
    meta = {"batch_id": batch_id, "source_video": "/videos/source.mp4"}
    meta.update(meta_overrides)
    item = ContentItem(title=f"{type_} item", type=type_, status=status, body="draft body", item_metadata=meta)
    db.session.add(item)
    db.session.commit()
    return item


def test_queue_requires_login(client):
    resp = client.get("/admin/content")
    assert resp.status_code == 302


def test_queue_lists_items_grouped_by_batch(app, client):
    with app.app_context():
        _make_item(batch_id="batchA")
        _make_item(batch_id="batchA", type_="caption")
        _make_item(batch_id="batchB", type_="newsletter")

    _login(client)
    resp = client.get("/admin/content")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "batchA" in body
    assert "batchB" in body
    assert "/videos/source.mp4" in body


def test_approve_transitions_draft_to_approved(app, client):
    with app.app_context():
        item = _make_item()
        item_id = item.id

    _login(client)
    resp = client.post(f"/admin/content/{item_id}/approve", follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        assert db.session.get(ContentItem, item_id).status == "approved"


def test_approve_refuses_non_draft_item(app, client):
    with app.app_context():
        item = _make_item(status="approved")
        item_id = item.id

    _login(client)
    client.post(f"/admin/content/{item_id}/approve", follow_redirects=True)

    with app.app_context():
        # still "approved" -- the guard didn't downgrade or double-approve it into
        # some other state, it just refused and flashed an error
        assert db.session.get(ContentItem, item_id).status == "approved"


def test_reject_sets_status_rejected(app, client):
    with app.app_context():
        item = _make_item()
        item_id = item.id

    _login(client)
    resp = client.post(f"/admin/content/{item_id}/reject", follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        assert db.session.get(ContentItem, item_id).status == "rejected"


def test_approve_all_bulk_approves_only_matching_batch(app, client):
    with app.app_context():
        a1 = _make_item(batch_id="batchA")
        a2 = _make_item(batch_id="batchA", type_="caption")
        b1 = _make_item(batch_id="batchB", type_="newsletter")
        a1_id, a2_id, b1_id = a1.id, a2.id, b1.id

    _login(client)
    resp = client.post("/admin/content/approve-all", data={"batch_id": "batchA"}, follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        assert db.session.get(ContentItem, a1_id).status == "approved"
        assert db.session.get(ContentItem, a2_id).status == "approved"
        assert db.session.get(ContentItem, b1_id).status == "draft"  # untouched -- different batch


def test_approve_all_only_touches_drafts(app, client):
    with app.app_context():
        already_approved = _make_item(batch_id="batchC", status="approved")
        approved_id = already_approved.id

    _login(client)
    client.post("/admin/content/approve-all", data={"batch_id": "batchC"}, follow_redirects=True)

    with app.app_context():
        # already approved before the bulk call -- approve_all only counts/flashes
        # drafts, so this should remain exactly "approved" (not double-processed)
        assert db.session.get(ContentItem, approved_id).status == "approved"
