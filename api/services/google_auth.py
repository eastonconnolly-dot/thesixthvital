"""Shared Google OAuth credential loader for Gmail + Calendar clients.

GOOGLE_OAUTH_CLIENT_JSON / GOOGLE_TOKEN_JSON may each be a filesystem path or
raw JSON (handy for Render env vars, which can't easily hold file uploads).
Run `flask google-auth` locally once to mint the token (see cli.py) before
deploying — Workspace credentials aren't something this session can generate
on your behalf.
"""

import json
import os

from flask import current_app
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
]


def _load_json_blob(value):
    if not value:
        return None
    if os.path.exists(value):
        with open(value) as f:
            return json.load(f)
    return json.loads(value)


def get_credentials():
    token_data = _load_json_blob(current_app.config["GOOGLE_TOKEN_JSON"])
    if not token_data:
        raise RuntimeError(
            "GOOGLE_TOKEN_JSON not configured. Run `flask google-auth` locally "
            "to authorize this app against the sending Workspace account, then "
            "set GOOGLE_TOKEN_JSON to the resulting token JSON."
        )
    creds = Credentials.from_authorized_user_info(token_data, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def run_local_oauth_flow():
    """Interactive, run once locally: `flask google-auth`. Prints the token
    JSON to store in GOOGLE_TOKEN_JSON."""
    client_config = _load_json_blob(current_app.config["GOOGLE_OAUTH_CLIENT_JSON"])
    if not client_config:
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_JSON not configured.")
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0)
    return creds.to_json()
