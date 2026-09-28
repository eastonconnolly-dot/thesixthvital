from services.pdf.badge import render_badge_pdf, render_badge_png
from services.pdf.proposal import render_proposal_pdf
from services.pdf.scorecard import render_cohort_scorecard_pdf, render_scorecard_pdf
from shared.rubric import DIMENSIONS


def _pdf_ok(buf):
    data = buf.read()
    assert data[:5] == b"%PDF-"
    assert len(data) > 500
    return data


def test_render_badge_pdf(app):
    with app.app_context():
        _pdf_ok(render_badge_pdf(participant_name="Priya Nair"))


def test_render_badge_png(app):
    with app.app_context():
        buf = render_badge_png(participant_name="Priya Nair")
        data = buf.read()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(data) > 500


def test_render_proposal_pdf(app):
    with app.app_context():
        buf = render_proposal_pdf(
            deal={"amount_cents": 1250000, "delivery_date": "2026-11-02"},
            lead={"name": "Dana Ortiz", "org": "Cascade Orthopedics"},
            package_label="Physician Private",
            deliverables=["Two-day intensive", "Baseline and final scoring"],
            deposit_link="https://checkout.stripe.com/test/abc",
            mailing_address="123 Main St, Spokane, WA",
        )
        _pdf_ok(buf)


def test_render_scorecard_pdf_single_participant(app):
    with app.app_context():
        baseline = {d: 2 for d in DIMENSIONS}
        final = {d: 4 for d in DIMENSIONS}
        buf = render_scorecard_pdf(
            session_label="Intensive Day 2",
            participant_name="Dana Ortiz",
            baseline_scores=baseline,
            final_scores=final,
            clip_timestamps=["00:04:12 — bad news delivery", "00:22:41 — mode shift"],
            mailing_address="123 Main St, Spokane, WA",
        )
        _pdf_ok(buf)


def test_render_cohort_scorecard_pdf(app):
    with app.app_context():
        baseline = {d: 2 for d in DIMENSIONS}
        final = {d: 4 for d in DIMENSIONS}
        buf = render_cohort_scorecard_pdf(
            session_label="Program Cohort — Day 1",
            participant_pairs=[("Dana Ortiz", baseline, final), ("Sam Lee", baseline, final)],
            mailing_address="123 Main St, Spokane, WA",
        )
        _pdf_ok(buf)
