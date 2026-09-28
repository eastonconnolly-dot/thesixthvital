"""The admin inbox screen itself lives at `api/routes/inbox.py` (a Flask
blueprint registered into the existing `api/` app) rather than here, per the
build brief: a separate Flask app for one admin screen would mean a second
session/auth story to maintain, when the existing admin blueprint's
`admin_required` decorator (`api/routes/admin.py`) already does the job.

This package is kept as a placeholder / landing spot for any inbox-adjacent,
non-Flask logic that might later want to live outside `api/routes/` (there
isn't any yet -- `outreach/engine/classifier.py` and
`outreach/engine/reply_detection.py` currently do all the actual work; the
route module just reads/writes the `MessageDraft` rows they produce).
"""
