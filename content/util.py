"""Small helpers shared by content/ingest.py, content/cut.py, content/schedule.py,
and content/send_newsletter.py.

Every script in this package is a standalone CLI meant to be run from the
repo root or from inside content/ — none of it is imported by the Flask app
itself (api/app.py isn't touched here; see content/INTEGRATION.md for the
one-line blueprint registration another engineer/deploy step should add).
Each script adds api/ to sys.path the same way api/app.py adds the repo root
(see its own `sys.path.insert` for `shared.*`), so `from app import
create_app`, `from extensions import db`, `from models import ...` etc. all
resolve the same way they do inside the Flask app.
"""

import os
import sys
from datetime import datetime, timezone


def add_api_to_path():
    api_dir = os.path.join(os.path.dirname(__file__), "..", "api")
    if api_dir not in sys.path:
        sys.path.insert(0, api_dir)


def get_app(config_object=None):
    """Builds the Flask app so CLI scripts can run inside app_context()
    without importing api/app.py at module import time (keeps `python -m
    pytest` from needing a real app instance just to import this module)."""
    add_api_to_path()
    from app import create_app
    if config_object:
        return create_app(config_object)
    return create_app()


def iso_week_of(d):
    """Returns 'YYYY-Www' for a date/datetime, ISO-8601 week numbering."""
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def current_iso_week():
    return iso_week_of(datetime.now(timezone.utc).date())


def next_thursday_iso_week(from_date=None):
    """The ISO week containing the next Thursday from from_date (today's
    Thursday counts). Newsletters are a weekly Thursday send per the brief;
    this is how ingest.py timestamps the newsletter ContentItem it creates
    so send_newsletter.py can find "this week's" newsletter later."""
    d = from_date or datetime.now(timezone.utc).date()
    days_ahead = (3 - d.weekday()) % 7  # Monday=0 ... Thursday=3 ... Sunday=6
    from datetime import timedelta
    thursday = d + timedelta(days=days_ahead)
    return iso_week_of(thursday)
