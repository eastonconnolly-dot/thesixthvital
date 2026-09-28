"""The RPSAS five-dimension encounter rubric. Framework-free — imported by
api/ (scorecards) and app/ (practice-session scoring)."""

DIMENSIONS = ("read_accuracy", "mode_match", "delivery", "adaptation", "outcome")
MIN_SCORE = 1
MAX_SCORE = 5
MAX_TOTAL = MAX_SCORE * len(DIMENSIONS)


class InvalidScoreError(ValueError):
    pass


def validate_scores(scores):
    """scores: dict mapping each of DIMENSIONS to an int 1-5. Raises
    InvalidScoreError on a missing dimension, unknown key, or out-of-range value."""
    missing = [d for d in DIMENSIONS if d not in scores]
    if missing:
        raise InvalidScoreError(f"missing dimensions: {missing}")
    unknown = [k for k in scores if k not in DIMENSIONS]
    if unknown:
        raise InvalidScoreError(f"unknown dimensions: {unknown}")
    for d in DIMENSIONS:
        v = scores[d]
        if not isinstance(v, int) or isinstance(v, bool):
            raise InvalidScoreError(f"{d} must be an int, got {v!r}")
        if not (MIN_SCORE <= v <= MAX_SCORE):
            raise InvalidScoreError(f"{d}={v} out of range [{MIN_SCORE},{MAX_SCORE}]")


def score_encounter(scores):
    """Returns {"total": int, "max": 25, "dimensions": {...}} for one encounter."""
    validate_scores(scores)
    return {
        "total": sum(scores[d] for d in DIMENSIONS),
        "max": MAX_TOTAL,
        "dimensions": {d: scores[d] for d in DIMENSIONS},
    }


def score_lift(baseline_scores, final_scores):
    """Returns per-dimension and total lift (final - baseline) between two
    encounters for the same participant."""
    baseline = score_encounter(baseline_scores)
    final = score_encounter(final_scores)
    return {
        "baseline_total": baseline["total"],
        "final_total": final["total"],
        "total_lift": final["total"] - baseline["total"],
        "dimensions": {
            d: final["dimensions"][d] - baseline["dimensions"][d] for d in DIMENSIONS
        },
    }


def cohort_lift(pairs):
    """pairs: iterable of (baseline_scores, final_scores) for a cohort.
    Returns aggregate average lift and the top gaps (dimensions with the
    smallest average final score, i.e. what to drill next)."""
    lifts = [score_lift(b, f) for b, f in pairs]
    if not lifts:
        return {"n": 0, "avg_total_lift": 0.0, "avg_final_by_dimension": {}, "top_gaps": []}
    n = len(lifts)
    avg_total_lift = sum(l["total_lift"] for l in lifts) / n
    avg_final_by_dim = {
        d: sum(score_encounter(f)["dimensions"][d] for _, f in pairs) / n for d in DIMENSIONS
    }
    top_gaps = sorted(avg_final_by_dim.items(), key=lambda kv: kv[1])[:3]
    return {
        "n": n,
        "avg_total_lift": avg_total_lift,
        "avg_final_by_dimension": avg_final_by_dim,
        "top_gaps": [d for d, _ in top_gaps],
    }
