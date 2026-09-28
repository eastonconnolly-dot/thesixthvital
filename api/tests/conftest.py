import os
import sys
import tempfile

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
    PRINT_VENDOR_EMAIL = "badges@example-vendor.test"
    UPLOAD_STORAGE_DIR = tempfile.mkdtemp(prefix="rpsas-test-uploads-")  # keep test uploads out of the repo tree


@pytest.fixture
def app():
    application = create_app(TestConfig)

    # content_admin (Phase 3) isn't registered in app.py yet — see
    # content/INTEGRATION.md — so it's wired in here for tests. Guarded so
    # this becomes a harmless no-op once app.py registers it for real.
    if "content_admin" not in application.blueprints:
        from routes.content_admin import bp as content_admin_bp
        application.register_blueprint(content_admin_bp)

    # Phase 6 qualifier + call_intake blueprints aren't registered in
    # app.py yet either — see api/INTEGRATION.md. Same guarded pattern.
    if "qualifier" not in application.blueprints:
        from routes.qualifier import bp as qualifier_bp
        application.register_blueprint(qualifier_bp)
    if "call_intake" not in application.blueprints:
        from routes.call_intake import bp as call_intake_bp
        application.register_blueprint(call_intake_bp)

    # Phase 6 playbook + closers blueprints (call playbook, sales handoff
    # readiness) aren't registered in app.py yet either — see this repo's
    # top-level INTEGRATION.md. Same guarded pattern.
    if "playbook" not in application.blueprints:
        from routes.playbook import bp as playbook_bp
        application.register_blueprint(playbook_bp)
    if "closers" not in application.blueprints:
        from routes.closers import bp as closers_bp
        application.register_blueprint(closers_bp)

    # Phase 6 onboarding + post-delivery automation blueprints aren't
    # registered in app.py yet either — see api/INTEGRATION.md. Same
    # guarded pattern.
    if "intake" not in application.blueprints:
        from routes.intake import bp as intake_bp
        application.register_blueprint(intake_bp)
    if "uploads" not in application.blueprints:
        from routes.uploads import bp as uploads_bp
        application.register_blueprint(uploads_bp)
    if "consent" not in application.blueprints:
        from routes.consent import bp as consent_bp
        application.register_blueprint(consent_bp)
    if "delivery" not in application.blueprints:
        from routes.delivery import bp as delivery_bp
        application.register_blueprint(delivery_bp)

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
