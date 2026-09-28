import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from app import create_app
from config import Config
from extensions import db as _db


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    ADMIN_PASSWORD = "test-admin-password"
    STRIPE_SECRET_KEY = ""  # empty -> stripe_client uses its stub path, no real network calls
    STRIPE_WEBHOOK_SECRET = "whsec_stub"
    ANTHROPIC_API_KEY = "sk-ant-test-stub"  # patient_sim calls are mocked in tests; this only satisfies the configured-check


@pytest.fixture
def app():
    application = create_app(TestConfig)
    with application.app_context():
        _db.create_all()
        yield application
        _db.session.remove()
        _db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    return _db
