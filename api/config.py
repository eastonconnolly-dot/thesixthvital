import os


class Config:
    BRAND_NAME = os.environ.get("BRAND_NAME", "RPSAS")
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(os.path.dirname(__file__), "dev.db")
    ).replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")

    GMAIL_SENDER_EMAIL = os.environ.get("GMAIL_SENDER_EMAIL", "")
    GOOGLE_OAUTH_CLIENT_JSON = os.environ.get("GOOGLE_OAUTH_CLIENT_JSON", "")
    GOOGLE_TOKEN_JSON = os.environ.get("GOOGLE_TOKEN_JSON", "")
    GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

    QUO_API_KEY = os.environ.get("QUO_API_KEY", "")

    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")

    PRACTICE_PRICES = {
        "applicant": {"label": "Applicant", "amount_cents": 4900, "stripe_price_id": os.environ.get("STRIPE_PRICE_APPLICANT", "")},
        "physician": {"label": "Physician", "amount_cents": 14900, "stripe_price_id": os.environ.get("STRIPE_PRICE_PHYSICIAN", "")},
        "program_seat": {"label": "Program Seat", "amount_cents": 19900, "stripe_price_id": os.environ.get("STRIPE_PRICE_PROGRAM_SEAT", "")},
    }
    PRACTICE_TRIAL_DAYS = 7

    COMPANY_MAILING_ADDRESS = os.environ.get(
        "COMPANY_MAILING_ADDRESS", "123 Main Street, Suite 100, Spokane, WA 99201"
    )
    SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "http://localhost:8010")
    API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:5000")

    DEPOSIT_FRACTION = 0.5
    BALANCE_DUE_DAYS_BEFORE = 14
    BALANCE_REMINDER_DAYS_BEFORE = 21
