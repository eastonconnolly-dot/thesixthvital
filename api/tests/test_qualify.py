from services.qualify import score_application


def test_qualified_when_budget_answered_track_and_org_present():
    score, qualified, budget_ok = score_application(
        "physician", "Cascade Orthopedics", {"budget_ok": "yes"}
    )
    assert qualified is True
    assert budget_ok is True
    assert score == 5  # budget_answered + track + org + budget_ok bonus(2)


def test_qualified_even_if_budget_not_yet_confirmed():
    # Per spec: "budget answered" (i.e. the question was answered) is the
    # gate — not that the answer was "yes".
    score, qualified, budget_ok = score_application(
        "applicant", "Some Med School", {"budget_ok": "no"}
    )
    assert qualified is True
    assert budget_ok is False
    assert score == 3


def test_not_qualified_without_org():
    score, qualified, budget_ok = score_application(
        "applicant", None, {"budget_ok": "yes"}
    )
    assert qualified is False


def test_not_qualified_without_budget_answer():
    score, qualified, budget_ok = score_application(
        "program", "Some Residency Program", {}
    )
    assert qualified is False
    assert budget_ok is None
