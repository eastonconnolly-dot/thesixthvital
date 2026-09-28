"""services/closers.py + routes/closers.py -- commission computed only at
the right stage transition (never before, never twice), and admin
auth-gating. No external calls involved (no Claude/Gmail/Stripe here), so
nothing needs mocking beyond ordinary DB fixtures."""

from extensions import db
from models import Closer, Deal, Lead
from services.closers import sync_commission, sync_commissions_for_closer


def _login(client):
    return client.post("/admin/login", data={"password": "test-admin-password"}, follow_redirects=True)


def _lead():
    lead = Lead(name="Dr. Test", email="dr@example.com", track="physician")
    db.session.add(lead)
    db.session.flush()
    return lead


def _closer(rate=0.10):
    closer = Closer(name="Sam Closer", email="sam@example.com", commission_rate=rate)
    db.session.add(closer)
    db.session.flush()
    return closer


def _deal(lead, closer=None, stage="discovery", amount_cents=1_000_000):
    deal = Deal(
        lead_id=lead.id, package="physician_private", amount_cents=amount_cents,
        balance_due_cents=0, stage=stage, closer_id=closer.id if closer else None,
    )
    db.session.add(deal)
    db.session.flush()
    return deal


# ── sync_commission: gating ────────────────────────────────────────────────

def test_no_commission_before_deposit_paid(app):
    with app.app_context():
        lead = _lead()
        closer = _closer(rate=0.10)
        deal = _deal(lead, closer, stage="proposal_sent", amount_cents=1_000_000)
        db.session.commit()

        sync_commission(deal)

        assert deal.commission_cents is None


def test_commission_computed_at_deposit_paid(app):
    with app.app_context():
        lead = _lead()
        closer = _closer(rate=0.10)
        deal = _deal(lead, closer, stage="deposit_paid", amount_cents=1_000_000)
        db.session.commit()

        sync_commission(deal)

        assert deal.commission_cents == 100_000  # 10% of $10,000.00


def test_commission_computed_at_delivered(app):
    with app.app_context():
        lead = _lead()
        closer = _closer(rate=0.20)
        deal = _deal(lead, closer, stage="delivered", amount_cents=500_000)
        db.session.commit()

        sync_commission(deal)

        assert deal.commission_cents == 100_000  # 20% of $5,000.00


def test_commission_not_recomputed_once_set(app):
    with app.app_context():
        lead = _lead()
        closer = _closer(rate=0.10)
        deal = _deal(lead, closer, stage="deposit_paid", amount_cents=1_000_000)
        db.session.commit()

        sync_commission(deal)
        assert deal.commission_cents == 100_000

        # Rate changes after the fact -- must NOT retroactively rewrite
        # commission already earned.
        closer.commission_rate = 0.50
        db.session.commit()
        sync_commission(deal)

        assert deal.commission_cents == 100_000


def test_no_commission_without_a_closer(app):
    with app.app_context():
        lead = _lead()
        deal = _deal(lead, closer=None, stage="deposit_paid", amount_cents=1_000_000)
        db.session.commit()

        sync_commission(deal)

        assert deal.commission_cents is None


def test_sync_commissions_for_closer_covers_all_their_deals(app):
    with app.app_context():
        lead = _lead()
        closer = _closer(rate=0.10)
        d1 = _deal(lead, closer, stage="deposit_paid", amount_cents=1_000_000)
        d2 = _deal(lead, closer, stage="discovery", amount_cents=2_000_000)
        db.session.commit()

        sync_commissions_for_closer(closer)

        assert d1.commission_cents == 100_000
        assert d2.commission_cents is None  # not at a qualifying stage yet


# ── admin routes: auth-gating ───────────────────────────────────────────────

def test_list_closers_requires_login(client):
    resp = client.get("/admin/closers")
    assert resp.status_code == 302


def test_create_closer_requires_login(client):
    resp = client.post("/admin/closers", data={"name": "X", "email": "x@example.com", "commission_rate": "0.1"})
    assert resp.status_code == 302


def test_assign_closer_requires_login(app, client):
    with app.app_context():
        lead = _lead()
        deal = _deal(lead)
        db.session.commit()
        deal_id = deal.id
    resp = client.post(f"/admin/deals/{deal_id}/assign-closer", data={"closer_id": ""})
    assert resp.status_code == 302


def test_calendar_requires_login(app, client):
    with app.app_context():
        closer = _closer()
        db.session.commit()
        closer_id = closer.id
    resp = client.get(f"/admin/closers/{closer_id}/calendar")
    assert resp.status_code == 302


# ── admin routes: behavior ──────────────────────────────────────────────────

def test_create_closer_success(app, client):
    _login(client)
    resp = client.post(
        "/admin/closers",
        data={"name": "Jamie Closer", "email": "jamie@example.com", "commission_rate": "0.15"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        closer = Closer.query.filter_by(email="jamie@example.com").first()
        assert closer is not None
        assert closer.commission_rate == 0.15


def test_create_closer_rejects_duplicate_email(app, client):
    with app.app_context():
        _closer()
        db.session.commit()
    _login(client)
    resp = client.post(
        "/admin/closers",
        data={"name": "Someone Else", "email": "sam@example.com", "commission_rate": "0.1"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    with app.app_context():
        assert Closer.query.filter_by(email="sam@example.com").count() == 1


def test_assign_closer_sets_closer_id_and_syncs_commission_if_already_closed(app, client):
    with app.app_context():
        lead = _lead()
        closer = _closer(rate=0.10)
        deal = _deal(lead, closer=None, stage="deposit_paid", amount_cents=1_000_000)
        db.session.commit()
        deal_id, closer_id = deal.id, closer.id

    _login(client)
    resp = client.post(f"/admin/deals/{deal_id}/assign-closer", data={"closer_id": str(closer_id)}, follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        deal = db.session.get(Deal, deal_id)
        assert deal.closer_id == closer_id
        # Deal was already at deposit_paid when assigned -- commission should
        # be computed immediately, not left dangling until the next view.
        assert deal.commission_cents == 100_000


def test_assign_closer_no_commission_yet_if_deal_not_closed(app, client):
    with app.app_context():
        lead = _lead()
        closer = _closer(rate=0.10)
        deal = _deal(lead, closer=None, stage="discovery", amount_cents=1_000_000)
        db.session.commit()
        deal_id, closer_id = deal.id, closer.id

    _login(client)
    client.post(f"/admin/deals/{deal_id}/assign-closer", data={"closer_id": str(closer_id)}, follow_redirects=True)

    with app.app_context():
        deal = db.session.get(Deal, deal_id)
        assert deal.closer_id == closer_id
        assert deal.commission_cents is None


def test_unassign_closer_clears_closer_id(app, client):
    with app.app_context():
        lead = _lead()
        closer = _closer()
        deal = _deal(lead, closer, stage="discovery")
        db.session.commit()
        deal_id = deal.id

    _login(client)
    client.post(f"/admin/deals/{deal_id}/assign-closer", data={"closer_id": ""}, follow_redirects=True)

    with app.app_context():
        deal = db.session.get(Deal, deal_id)
        assert deal.closer_id is None


def test_calendar_lists_only_that_closers_deals_and_syncs_commission(app, client):
    with app.app_context():
        lead = _lead()
        closer_a = _closer(rate=0.10)
        closer_b = Closer(name="Other Closer", email="other@example.com", commission_rate=0.10)
        db.session.add(closer_b)
        db.session.flush()
        deal_a = _deal(lead, closer_a, stage="deposit_paid", amount_cents=1_000_000)
        deal_b = _deal(lead, closer_b, stage="deposit_paid", amount_cents=1_000_000)
        db.session.commit()
        closer_a_id, deal_a_id, deal_b_id = closer_a.id, deal_a.id, deal_b.id

    _login(client)
    resp = client.get(f"/admin/closers/{closer_a_id}/calendar")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert f"#{deal_a_id}</td>" in body
    assert f"#{deal_b_id}</td>" not in body

    with app.app_context():
        # Viewing the calendar should have synced commission for deal_a
        # (already at deposit_paid) without touching deal_b (different closer).
        assert db.session.get(Deal, deal_a_id).commission_cents == 100_000
