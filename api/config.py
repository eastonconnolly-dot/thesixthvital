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

    # Content engine (Phase 3). Transcription is local/self-hosted (faster-whisper
    # or openai-whisper -- see content/ingest.py), so there's no vendor key for
    # it. Buffer/Publer are the one named SaaS exception for scheduling, since
    # LinkedIn's own API can't post to a personal profile -- both optional, and
    # content/schedule.py degrades to a local-only "scheduled" mark if neither
    # is set (see its module docstring).
    BUFFER_API_KEY = os.environ.get("BUFFER_API_KEY", "")
    PUBLER_API_KEY = os.environ.get("PUBLER_API_KEY", "")
    WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "base")
    CONTENT_UPLOADS_DIR = os.environ.get(
        "CONTENT_UPLOADS_DIR", os.path.join(os.path.dirname(__file__), "..", "content", "output", "uploads")
    )
    CONTENT_CLIPS_DIR = os.environ.get(
        "CONTENT_CLIPS_DIR", os.path.join(os.path.dirname(__file__), "..", "content", "output", "clips")
    )

    # ── Phase 2: outreach list builders / enrichment ─────────────────────
    GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")
    HUNTER_API_KEY = os.environ.get("HUNTER_API_KEY", "")
    APOLLO_API_KEY = os.environ.get("APOLLO_API_KEY", "")

    # Sequence engine throttling: 80/day/mailbox, ramped over two weeks.
    SEQUENCE_DAILY_SEND_CAP = int(os.environ.get("SEQUENCE_DAILY_SEND_CAP", "80"))
    SEQUENCE_RAMP_DAYS = int(os.environ.get("SEQUENCE_RAMP_DAYS", "14"))
    SEQUENCE_RAMP_START_CAP = int(os.environ.get("SEQUENCE_RAMP_START_CAP", "10"))

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

    # ── Phase 5: ops (weekly digest, nightly backups, error alerts) ──────
    # Founder's own inbox — destination for both the Monday digest and any
    # error_alerts.alert() call. No stub mode: unset means those sends
    # raise a clear RuntimeError rather than silently going nowhere.
    FOUNDER_EMAIL = os.environ.get("FOUNDER_EMAIL", "")

    # Nightly DB backup upload target — pick ONE:
    #   (a) a private GitHub release, via the `gh` CLI (must be installed and
    #       `gh auth login`'d in whatever environment runs `flask backup-db`)
    BACKUP_GITHUB_REPO = os.environ.get("BACKUP_GITHUB_REPO", "")  # "owner/repo"
    #   (b) an S3-compatible bucket, via boto3
    AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
    AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    BACKUP_BUCKET = os.environ.get("BACKUP_BUCKET", "")
    BACKUP_S3_ENDPOINT_URL = os.environ.get("BACKUP_S3_ENDPOINT_URL", "")  # non-AWS S3-compatible (R2, Backblaze, etc.); blank = real AWS S3

    # ── Hub tenant sync (see HUB_INTEGRATION.md) ──────────────────────────
    # RPSAS's leads/deals still live in this app's own DB (leads/deals below
    # aren't going anywhere) -- this pushes a copy into the Hub as a real
    # customer record on RPSAS's own tenant, so the founder can see RPSAS
    # leads inside the same CRM as every other business. Fully optional and
    # no-ops cleanly (see services/hub_sync.py) until HUB_API_KEY and at
    # least one HUB_PIPELINE_KEY_* are set -- none of that exists until
    # scripts/provision_rpsas_tenant.py has actually been run against the Hub.
    HUB_API_BASE_URL = os.environ.get("HUB_API_BASE_URL", "")
    HUB_API_KEY = os.environ.get("HUB_API_KEY", "")
    HUB_PIPELINE_KEYS = {
        "applicant": os.environ.get("HUB_PIPELINE_KEY_APPLICANT", ""),
        "physician": os.environ.get("HUB_PIPELINE_KEY_PHYSICIAN", ""),
        "program": os.environ.get("HUB_PIPELINE_KEY_PROGRAM", ""),
    }
