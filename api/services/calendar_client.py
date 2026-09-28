"""Google Calendar free/busy + a simple slot picker — replaces Calendly per
the non-negotiables (no CRM/scheduling SaaS)."""

from datetime import datetime, timedelta, timezone

from flask import current_app
from googleapiclient.discovery import build

from .google_auth import get_credentials

WORKDAY_START_HOUR = 9
WORKDAY_END_HOUR = 17
SLOT_MINUTES = 30


def _service():
    return build("calendar", "v3", credentials=get_credentials())


def get_busy_windows(start, end):
    service = _service()
    calendar_id = current_app.config["GOOGLE_CALENDAR_ID"]
    body = {
        "timeMin": start.isoformat(),
        "timeMax": end.isoformat(),
        "items": [{"id": calendar_id}],
    }
    result = service.freebusy().query(body=body).execute()
    busy = result["calendars"][calendar_id]["busy"]
    return [
        (datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
        for b in busy
    ]


def _overlaps(slot_start, slot_end, busy_windows):
    return any(slot_start < b_end and slot_end > b_start for b_start, b_end in busy_windows)


def available_slots(days_ahead=10, tz=timezone.utc):
    now = datetime.now(tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days_ahead)
    busy = get_busy_windows(start, end)

    slots = []
    day = start
    while day < end:
        if day.weekday() < 5:  # Mon-Fri
            slot_start = day.replace(hour=WORKDAY_START_HOUR)
            day_end = day.replace(hour=WORKDAY_END_HOUR)
            while slot_start + timedelta(minutes=SLOT_MINUTES) <= day_end:
                slot_end = slot_start + timedelta(minutes=SLOT_MINUTES)
                if slot_start > now and not _overlaps(slot_start, slot_end, busy):
                    slots.append(slot_start)
                slot_start = slot_end
        day += timedelta(days=1)
    return slots


def book_slot(slot_start, summary, attendee_email):
    service = _service()
    calendar_id = current_app.config["GOOGLE_CALENDAR_ID"]
    slot_end = slot_start + timedelta(minutes=SLOT_MINUTES)
    event = {
        "summary": summary,
        "start": {"dateTime": slot_start.isoformat()},
        "end": {"dateTime": slot_end.isoformat()},
        "attendees": [{"email": attendee_email}],
    }
    return service.events().insert(calendarId=calendar_id, body=event, sendUpdates="all").execute()
