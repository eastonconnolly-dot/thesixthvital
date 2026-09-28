import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest

from shared.rubric import (
    DIMENSIONS, InvalidScoreError, cohort_lift, score_encounter, score_lift,
)

FULL_SCORES = {d: 3 for d in DIMENSIONS}


def test_score_encounter_totals_correctly():
    result = score_encounter({d: 4 for d in DIMENSIONS})
    assert result["total"] == 20
    assert result["max"] == 25


def test_score_encounter_rejects_missing_dimension():
    incomplete = {d: 3 for d in DIMENSIONS if d != "outcome"}
    with pytest.raises(InvalidScoreError):
        score_encounter(incomplete)


def test_score_encounter_rejects_unknown_dimension():
    bad = dict(FULL_SCORES)
    bad["not_a_real_dimension"] = 3
    with pytest.raises(InvalidScoreError):
        score_encounter(bad)


@pytest.mark.parametrize("bad_value", [0, 6, -1, 3.5])
def test_score_encounter_rejects_out_of_range(bad_value):
    bad = dict(FULL_SCORES)
    bad["outcome"] = bad_value
    with pytest.raises(InvalidScoreError):
        score_encounter(bad)


def test_score_encounter_rejects_bool_as_int():
    bad = dict(FULL_SCORES)
    bad["outcome"] = True
    with pytest.raises(InvalidScoreError):
        score_encounter(bad)


def test_score_lift_positive_improvement():
    baseline = {d: 2 for d in DIMENSIONS}
    final = {d: 4 for d in DIMENSIONS}
    lift = score_lift(baseline, final)
    assert lift["baseline_total"] == 10
    assert lift["final_total"] == 20
    assert lift["total_lift"] == 10
    assert all(v == 2 for v in lift["dimensions"].values())


def test_score_lift_can_be_negative():
    baseline = {d: 4 for d in DIMENSIONS}
    final = {d: 2 for d in DIMENSIONS}
    lift = score_lift(baseline, final)
    assert lift["total_lift"] == -10


def test_cohort_lift_empty():
    result = cohort_lift([])
    assert result["n"] == 0
    assert result["avg_total_lift"] == 0.0
    assert result["top_gaps"] == []


def test_cohort_lift_identifies_top_gaps():
    pairs = [
        ({d: 2 for d in DIMENSIONS}, {"read_accuracy": 5, "mode_match": 5, "delivery": 2, "adaptation": 2, "outcome": 2}),
        ({d: 2 for d in DIMENSIONS}, {"read_accuracy": 5, "mode_match": 5, "delivery": 2, "adaptation": 2, "outcome": 2}),
    ]
    result = cohort_lift(pairs)
    assert result["n"] == 2
    assert set(result["top_gaps"]) == {"delivery", "adaptation", "outcome"}
    assert result["avg_total_lift"] == pytest.approx(6.0)
