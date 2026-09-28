"""The sending/receiving half of Phase 2: the sequence-tick cron advancer,
Gmail reply detection, and the positive-reply classifier + draft-reply
writer that feeds the admin inbox (`api/routes/inbox.py`).

These modules need the Flask app (DB session, config, `api/services/*`
clients), so each defensively puts `api/` on `sys.path` if it isn't already
there -- see `outreach/__init__.py`'s docstring for why."""
