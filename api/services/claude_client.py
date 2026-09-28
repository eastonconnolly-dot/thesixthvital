"""Thin Anthropic client wrapper. Model is configurable (cost matters at
$49-199/mo pricing with many turns per session) — override via CLAUDE_MODEL
if you want higher-fidelity roleplay at higher cost."""

import anthropic
from flask import current_app


def client():
    return anthropic.Anthropic(api_key=current_app.config["ANTHROPIC_API_KEY"] or None)


def model():
    return current_app.config["CLAUDE_MODEL"]
