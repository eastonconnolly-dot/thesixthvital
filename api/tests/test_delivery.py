from datetime import datetime, timezone
from unittest.mock import patch

from extensions import db
from models import ConsentRequest, Deal, EncounterSession, Lead, PracticeUser, ScheduledFollowup, Scorecard
from services.delivery import complete_session, send_scheduled_followups
from shared.rubric import DIMENSIONS


def _login(client):
    return client.post("/admin/login", data={"password": "test-admin-password"}, follow_redirects=True)


def _make_deal(app, track="physician", package="physician_private"):
    lead = Lead(name="Dana Ortiz", email="dana@example.com", track=track)
    db.session.add(lead)
    db.session.flush()
    deal = Deal(lead_id=lead.id, package=package, amount_cents=1250000, balance_due_cents=1250000, stage="scheduled")
    db.session.add(deal)
    db.session.commit()
    return deal


def _participant(name, email, base=2, fin=4):
    return {
        "name": name, "email": email,
        "baseline": {d: base for d in DIMENSIONS},
        "final": {d: fin for d in DIMENSIONS},
    }


_SEND_EMAIL_STUB = {"message_id": "m1", "thread_id": "t1"}


# ── services.delivery.complete_session ────────────────────────────────────

def test_complete_session_creates_scorecards_and_provisions_seats(app):
    with app.app_context():
        deal = _make_deal(app)
        participants = [_participant("Dana Ortiz", "dana@example.com")]

        with patch("services.delivery.gmail_client.send_email", return_value=_SEND_EMAIL_STUB) as mock_send:
            result = complete_session(deal, None, "intensive_day_1", participants)

        assert result["status"] == "ok"
        assert result["scorecards_sent"] == 1
        assert result["practice_seats_provisioned"] == 1
        assert result["followups_scheduled"] == 2
        assert result["consent_requests_created"] == 2
        assert result["cohort_sent"] is False  # only one participant

        deal = db.session.get(Deal, deal.id)
        assert deal.stage == "delivered"
        assert deal.delivered_at is not None

        assert EncounterSession.query.filter_by(deal_id=deal.id).count() == 1
        session = EncounterSession.query.filter_by(deal_id=deal.id).first()
        assert Scorecard.query.filter_by(session_id=session.id).count() == 1
        sc = Scorecard.query.filter_by(session_id=session.id).first()
        assert sc.lift["total_lift"] == 10  # (4-2)*5 dimensions

        assert PracticeUser.query.filter_by(email="dana@example.com").count() == 1
        assert ConsentRequest.query.filter_by(scorecard_id=sc.id).count() == 2
        assert {cr.kind for cr in ConsentRequest.query.filter_by(scorecard_id=sc.id).all()} == {"testimonial", "clip_consent"}
        assert ScheduledFollowup.query.filter_by(deal_id=deal.id).count() == 2

        # scorecard email got a PDF attachment
        scorecard_calls = [c for c in mock_send.call_args_list if "attachments" in c.kwargs and c.kwargs["attachments"]]
        assert any(kw.kwargs["subject"].startswith("Your") and "scorecard" in kw.kwargs["subject"] for kw in scorecard_calls)


def test_complete_session_is_idempotent(app):
    with app.app_context():
        deal = _make_deal(app)
        participants = [_participant("Dana Ortiz", "dana@example.com")]
        with patch("services.delivery.gmail_client.send_email", return_value=_SEND_EMAIL_STUB) as mock_send:
            first = complete_session(deal, None, "intensive_day_1", participants)
            second = complete_session(deal, None, "intensive_day_1", participants)

        assert first["status"] == "ok"
        assert second == {"status": "already_delivered"}
        assert EncounterSession.query.filter_by(deal_id=deal.id).count() == 1


def test_complete_session_sends_cohort_pdf_for_multiple_participants(app):
    with app.app_context():
        deal = _make_deal(app)
        participants = [
            _participant("Dana Ortiz", "dana@example.com"),
            _participant("Sam Lee", "sam@example.com"),
        ]
        with patch("services.delivery.gmail_client.send_email", return_value=_SEND_EMAIL_STUB) as mock_send:
            result = complete_session(deal, None, "intensive_day_1", participants)

        assert result["cohort_sent"] is True
        assert result["program_deck_sent"] is False  # not a program-track deal

        cohort_calls = [c for c in mock_send.call_args_list if "cohort score report" in c.kwargs["subject"].lower()]
        assert len(cohort_calls) == 1
        assert cohort_calls[0].kwargs["to_email"] == "dana@example.com"  # the deal's own lead / sponsor


def test_complete_session_sends_program_deck_for_program_track(app):
    with app.app_context():
        deal = _make_deal(app, track="program", package="program_cohort_1day")
        participants = [
            _participant("Alex Kim", "alex@example.com"),
            _participant("Jordan Lee", "jordan@example.com"),
        ]
        with patch("services.delivery.gmail_client.send_email", return_value=_SEND_EMAIL_STUB) as mock_send:
            result = complete_session(deal, None, "cohort_day_1", participants)

        assert result["program_deck_sent"] is True
        deck_calls = [c for c in mock_send.call_args_list if "leadership deck" in c.kwargs["subject"].lower()]
        assert len(deck_calls) == 1


# ── admin routes ────────────────────────────────────────────────────────

def test_delivery_form_requires_login(client):
    resp = client.get("/admin/delivery/1")
    assert resp.status_code == 302


def test_delivery_form_renders(app, client):
    with app.app_context():
        deal = _make_deal(app)
        deal_id = deal.id
    _login(client)
    resp = client.get(f"/admin/delivery/{deal_id}")
    assert resp.status_code == 200
    assert b"Dana Ortiz" in resp.data


def test_delivery_complete_route_creates_session(app, client):
    with app.app_context():
        deal = _make_deal(app)
        deal_id = deal.id
    _login(client)

    form_data = {
        "session_date": "2026-10-05", "session_type": "intensive_day_1",
        "participant_name": ["Dana Ortiz"], "participant_email": ["dana@example.com"],
    }
    for dim in DIMENSIONS:
        form_data[f"baseline_{dim}_0"] = "2"
        form_data[f"final_{dim}_0"] = "4"

    with patch("routes.delivery.complete_session") as mock_complete:
        mock_complete.return_value = {
            "status": "ok", "session_id": 1, "scorecards_sent": 1, "cohort_sent": False,
            "consent_requests_created": 2, "practice_seats_provisioned": 1, "followups_scheduled": 2,
            "program_deck_sent": False,
        }
        resp = client.post(f"/admin/delivery/{deal_id}/complete", data=form_data, follow_redirects=True)

    assert resp.status_code == 200
    mock_complete.assert_called_once()
    called_deal, called_date, called_type, called_participants = mock_complete.call_args[0]
    assert called_deal.id == deal_id
    assert called_type == "intensive_day_1"
    assert called_participants[0]["name"] == "Dana Ortiz"
    assert called_participants[0]["baseline"]["read_accuracy"] == 2


def test_delivery_complete_rejects_missing_scores(app, client):
    with app.app_context():
        deal = _make_deal(app)
        deal_id = deal.id
    _login(client)

    resp = client.post(f"/admin/delivery/{deal_id}/complete", data={
        "session_date": "2026-10-05", "session_type": "intensive_day_1",
        "participant_name": ["Dana Ortiz"], "participant_email": ["dana@example.com"],
        # scores omitted
    }, follow_redirects=True)
    assert resp.status_code == 200
    assert EncounterSession.query.filter_by(deal_id=deal_id).count() == 0


def test_delivery_complete_rejects_when_already_delivered(app, client):
    with app.app_context():
        deal = _make_deal(app)
        deal.delivered_at = datetime.now(timezone.utc)
        db.session.commit()
        deal_id = deal.id
    _login(client)

    form_data = {
        "session_date": "2026-10-05", "session_type": "intensive_day_1",
        "participant_name": ["Dana Ortiz"], "participant_email": ["dana@example.com"],
    }
    for dim in DIMENSIONS:
        form_data[f"baseline_{dim}_0"] = "2"
        form_data[f"final_{dim}_0"] = "4"

    resp = client.post(f"/admin/delivery/{deal_id}/complete", data=form_data, follow_redirects=True)
    assert resp.status_code == 200
    assert EncounterSession.query.filter_by(deal_id=deal_id).count() == 0


def test_approve_testimonial_flips_approved_flag(app, client):
    with app.app_context():
        deal = _make_deal(app)
        session = EncounterSession(deal_id=deal.id, participants=[])
        db.session.add(session)
        db.session.flush()
        sc = Scorecard(session_id=session.id, participant_name="Dana Ortiz", participant_email="dana@example.com")
        db.session.add(sc)
        db.session.flush()
        cr = ConsentRequest(
            scorecard_id=sc.id, session_id=session.id, kind="testimonial", token="consent-tok-1",
            granted=True, response_text="Loved it.",
        )
        db.session.add(cr)
        db.session.commit()
        cr_id = cr.id

    _login(client)
    resp = client.post(f"/admin/delivery/testimonials/{cr_id}/approve", follow_redirects=True)
    assert resp.status_code == 200

    with app.app_context():
        assert ConsentRequest.query.get(cr_id).approved is True


def test_approve_testimonial_rejects_ungranted(app, client):
    with app.app_context():
        deal = _make_deal(app)
        session = EncounterSession(deal_id=deal.id, participants=[])
        db.session.add(session)
        db.session.flush()
        sc = Scorecard(session_id=session.id, participant_name="Dana Ortiz", participant_email="dana@example.com")
        db.session.add(sc)
        db.session.flush()
        cr = ConsentRequest(
            scorecard_id=sc.id, session_id=session.id, kind="testimonial", token="consent-tok-2", granted=False,
        )
        db.session.add(cr)
        db.session.commit()
        cr_id = cr.id

    _login(client)
    client.post(f"/admin/delivery/testimonials/{cr_id}/approve", follow_redirects=True)

    with app.app_context():
        assert ConsentRequest.query.get(cr_id).approved is False


# ── consent respond routes ─────────────────────────────────────────────

def test_consent_respond_round_trip(app, client):
    with app.app_context():
        deal = _make_deal(app)
        session = EncounterSession(deal_id=deal.id, participants=[])
        db.session.add(session)
        db.session.flush()
        sc = Scorecard(session_id=session.id, participant_name="Dana Ortiz", participant_email="dana@example.com")
        db.session.add(sc)
        db.session.flush()
        db.session.add(ConsentRequest(
            scorecard_id=sc.id, session_id=session.id, kind="testimonial", token="respond-tok-1",
        ))
        db.session.commit()

    resp = client.get("/consent/respond-tok-1")
    assert resp.status_code == 200

    resp = client.post("/consent/respond-tok-1", data={"granted": "yes", "response_text": "Great experience!"})
    assert resp.status_code == 200
    assert b"recorded" in resp.data.lower()

    with app.app_context():
        cr = ConsentRequest.query.filter_by(token="respond-tok-1").first()
        assert cr.granted is True
        assert cr.response_text == "Great experience!"
        assert cr.responded_at is not None

    second = client.post("/consent/respond-tok-1", data={"granted": "no"})
    assert second.status_code == 409


def test_consent_respond_404_for_unknown_token(client):
    assert client.get("/consent/not-a-real-token").status_code == 404


def test_refer_page_records_click(app, client):
    with app.app_context():
        lead = Lead(name="Dana Ortiz", email="dana@example.com", track="physician")
        db.session.add(lead)
        db.session.commit()
        lead_id = lead.id

    resp = client.get(f"/refer/{lead_id}")
    assert resp.status_code == 200

    with app.app_context():
        from models import ReferralClick
        assert ReferralClick.query.filter_by(lead_id=lead_id).count() == 1


def test_refer_page_404_for_unknown_lead(client):
    assert client.get("/refer/999999").status_code == 404


# ── send_scheduled_followups ────────────────────────────────────────────

def test_send_scheduled_followups_sends_only_due_items(app):
    with app.app_context():
        deal = _make_deal(app)
        lead = deal.lead
        now = datetime(2026, 9, 27, tzinfo=timezone.utc)

        due_checkin = ScheduledFollowup(deal_id=deal.id, lead_id=lead.id, kind="checkin_30day", due_at=now)
        due_referral = ScheduledFollowup(deal_id=deal.id, lead_id=lead.id, kind="referral_ask", due_at=now)
        not_due = ScheduledFollowup(deal_id=deal.id, lead_id=lead.id, kind="checkin_30day", due_at=datetime(2026, 10, 5, tzinfo=timezone.utc))
        db.session.add_all([due_checkin, due_referral, not_due])
        db.session.commit()

        with patch("services.delivery.gmail_client.send_email", return_value=_SEND_EMAIL_STUB) as mock_send:
            result = send_scheduled_followups(now=now)

        assert result["checkin_sent"] == 1
        assert result["referral_sent"] == 1
        assert mock_send.call_count == 2

        assert db.session.get(ScheduledFollowup, due_checkin.id).sent_at is not None
        assert db.session.get(ScheduledFollowup, due_referral.id).sent_at is not None
        assert db.session.get(ScheduledFollowup, not_due.id).sent_at is None


def test_send_scheduled_followups_does_not_resend(app):
    with app.app_context():
        deal = _make_deal(app)
        lead = deal.lead
        now = datetime(2026, 9, 27, tzinfo=timezone.utc)
        followup = ScheduledFollowup(deal_id=deal.id, lead_id=lead.id, kind="referral_ask", due_at=now)
        db.session.add(followup)
        db.session.commit()

        with patch("services.delivery.gmail_client.send_email", return_value=_SEND_EMAIL_STUB) as mock_send:
            send_scheduled_followups(now=now)
            result = send_scheduled_followups(now=now)

        assert result["referral_sent"] == 0
        assert mock_send.call_count == 1
