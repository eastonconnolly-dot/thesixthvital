"""services/call_playbook.py -- the <10-calls "not enough data" path, the
defensive no-op when no call-transcript model exists yet, playbook
generation/persistence, and the admin routes. All Claude calls are mocked
(either at _plan_playbook directly, or one level down at claude_client, to
also exercise the JSON-schema-constrained call shape)."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from extensions import db
from models import CallPlaybook
from services import call_playbook


def _login(client):
    return client.post("/admin/login", data={"password": "test-admin-password"}, follow_redirects=True)


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Row:
    def __init__(self, transcript):
        self.transcript = transcript


def _install_fake_sales_call(monkeypatch, transcripts):
    import models

    class FakeSalesCall:
        query = _FakeQuery([_Row(t) for t in transcripts])

    monkeypatch.setattr(models, "SalesCall", FakeSalesCall, raising=False)
    return FakeSalesCall


FAKE_PLAN = {
    "objections": [
        {"objection": "This seems expensive.", "response": "Walk through the ROI of one avoided complaint."},
        {"objection": "I need to check with my partner.", "response": "Offer to include them on a short call."},
    ],
    "close_lines": ["Let's get your deposit in so we can lock the date.", "Ready to move forward today?"],
}


# ── defensive no-op when SalesCall doesn't exist ───────────────────────────

def test_generate_playbook_noop_when_model_absent(app, monkeypatch):
    import models
    monkeypatch.delattr(models, "SalesCall", raising=False)
    with app.app_context():
        result = call_playbook.generate_playbook()
    assert result == {"available": False, "reason": "no call-transcript source configured yet", "playbook": None}
    assert CallPlaybook.query.count() == 0


def test_transcript_status_reports_absent(app, monkeypatch):
    import models
    monkeypatch.delattr(models, "SalesCall", raising=False)
    with app.app_context():
        available, count = call_playbook.transcript_status()
    assert available is False
    assert count == 0


# ── <10 calls: "not enough data yet" ───────────────────────────────────────

def test_generate_playbook_not_enough_data(app, monkeypatch):
    with app.app_context():
        _install_fake_sales_call(monkeypatch, ["call 1", "call 2", "call 3"])
        with patch("services.call_playbook._plan_playbook") as mock_plan:
            result = call_playbook.generate_playbook()

    mock_plan.assert_not_called()  # must not call Claude on a thin sample
    assert result["available"] is True
    assert result["playbook"] is None
    assert result["transcript_count"] == 3
    assert "3/10" in result["reason"]
    assert CallPlaybook.query.count() == 0


def test_generate_playbook_ignores_rows_with_no_transcript_yet(app, monkeypatch):
    with app.app_context():
        transcripts = [f"call {i}" for i in range(9)] + [None, ""]  # 9 real + 2 empty
        _install_fake_sales_call(monkeypatch, transcripts)
        result = call_playbook.generate_playbook()

    assert result["transcript_count"] == 9  # None/"" filtered out
    assert "9/10" in result["reason"]


# ── >=10 calls: generates and persists ─────────────────────────────────────

def test_generate_playbook_persists_row_with_enough_transcripts(app, monkeypatch):
    with app.app_context():
        transcripts = [f"call transcript {i}" for i in range(12)]
        _install_fake_sales_call(monkeypatch, transcripts)
        with patch("services.call_playbook._plan_playbook", return_value=FAKE_PLAN) as mock_plan:
            result = call_playbook.generate_playbook()

        mock_plan.assert_called_once()
        assert result["available"] is True
        assert result["transcript_count"] == 12
        row = result["playbook"]
        assert row.id is not None
        assert row.transcript_count == 12
        assert row.objections == FAKE_PLAN["objections"]
        assert row.close_lines == FAKE_PLAN["close_lines"]

        assert CallPlaybook.query.count() == 1
        assert call_playbook.latest_playbook().id == row.id


def test_generate_playbook_regenerate_creates_new_row(app, monkeypatch):
    with app.app_context():
        transcripts = [f"call {i}" for i in range(10)]
        _install_fake_sales_call(monkeypatch, transcripts)
        with patch("services.call_playbook._plan_playbook", return_value=FAKE_PLAN):
            call_playbook.generate_playbook()
            call_playbook.generate_playbook()
        assert CallPlaybook.query.count() == 2  # one row per generation, not overwritten


# ── _plan_playbook: real schema-call wiring, Claude mocked at the client ──

def test_plan_playbook_calls_claude_with_json_schema_and_parses_response(app):
    fake_resp = SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(FAKE_PLAN))])
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_resp

    with app.app_context():
        with patch("services.call_playbook.claude_client.client", return_value=fake_client), \
             patch("services.call_playbook.claude_client.model", return_value="claude-sonnet-5"):
            result = call_playbook._plan_playbook(["transcript one", "transcript two"])

    assert result == FAKE_PLAN
    _, kwargs = fake_client.messages.create.call_args
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    assert kwargs["output_config"]["format"]["schema"] == call_playbook.PLAYBOOK_SCHEMA


# ── admin routes ────────────────────────────────────────────────────────

def test_playbook_page_requires_login(client):
    resp = client.get("/admin/playbook")
    assert resp.status_code == 302


def test_playbook_page_shows_not_enough_data(app, client, monkeypatch):
    with app.app_context():
        _install_fake_sales_call(monkeypatch, ["call 1", "call 2"])
    _login(client)
    resp = client.get("/admin/playbook")
    assert resp.status_code == 200
    assert b"2/10" in resp.data


def test_regenerate_requires_login(client):
    resp = client.post("/admin/playbook/regenerate")
    assert resp.status_code == 302


def test_regenerate_flashes_error_when_not_enough_data(app, client, monkeypatch):
    with app.app_context():
        _install_fake_sales_call(monkeypatch, ["only one call"])
    _login(client)
    resp = client.post("/admin/playbook/regenerate", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Not enough transcribed calls" in resp.data


def test_regenerate_succeeds_and_shows_playbook(app, client, monkeypatch):
    with app.app_context():
        transcripts = [f"call {i}" for i in range(10)]
        _install_fake_sales_call(monkeypatch, transcripts)
    _login(client)
    with patch("services.call_playbook._plan_playbook", return_value=FAKE_PLAN):
        resp = client.post("/admin/playbook/regenerate", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Playbook regenerated" in resp.data
    assert b"This seems expensive." in resp.data
