"""Application qualification scoring: budget answered + track + org present = qualified."""


def score_application(track, org, answers):
    """answers: dict from the apply form, expected to include 'budget_ok'
    ('yes'|'no'|'unsure') and 'situation' (free text). Returns (score, qualified,
    budget_ok_bool_or_none)."""
    budget_answer = (answers or {}).get("budget_ok")
    budget_answered = budget_answer in ("yes", "no", "unsure")
    budget_ok = True if budget_answer == "yes" else (False if budget_answer == "no" else None)

    track_present = bool(track)
    org_present = bool(org and org.strip())

    qualified = bool(budget_answered and track_present and org_present)

    score = 0
    if budget_answered:
        score += 1
    if track_present:
        score += 1
    if org_present:
        score += 1
    if budget_ok is True:
        score += 2  # budget confirmed is the strongest signal, weighted extra

    return score, qualified, budget_ok
