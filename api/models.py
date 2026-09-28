from datetime import datetime, timezone

from extensions import db


def utcnow():
    return datetime.now(timezone.utc)


def _aware(dt):
    """SQLite drops tzinfo on read even for DateTime(timezone=True) columns;
    Postgres doesn't. Normalize so comparisons work on both backends."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class Lead(db.Model):
    __tablename__ = "leads"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(320), nullable=False, index=True)
    phone = db.Column(db.String(40))
    track = db.Column(db.String(20), nullable=False)  # applicant | physician | program
    source = db.Column(db.String(60), nullable=False, default="site_apply")
    org = db.Column(db.String(300))
    role = db.Column(db.String(200))
    state = db.Column(db.String(2))
    status = db.Column(db.String(30), nullable=False, default="new")
    # new -> qualified|nurture -> booked -> customer  (or unsubscribed/bounced)
    tags = db.Column(db.JSON, nullable=False, default=list)
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    hub_customer_id = db.Column(db.Integer)  # set once services/hub_sync.py has pushed this lead into the Hub

    applications = db.relationship("Application", backref="lead", lazy=True)
    deals = db.relationship("Deal", backref="lead", lazy=True)
    enrollments = db.relationship("SequenceEnrollment", backref="lead", lazy=True)
    messages = db.relationship("Message", backref="lead", lazy=True)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "phone": self.phone,
            "track": self.track,
            "source": self.source,
            "org": self.org,
            "role": self.role,
            "state": self.state,
            "status": self.status,
            "tags": self.tags or [],
            "created": self.created.isoformat() if self.created else None,
        }


TRACKS = ("applicant", "physician", "program")


class Application(db.Model):
    __tablename__ = "applications"

    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=False)
    answers = db.Column(db.JSON, nullable=False, default=dict)
    budget_ok = db.Column(db.Boolean)
    score = db.Column(db.Integer, nullable=False, default=0)
    qualified = db.Column(db.Boolean, nullable=False, default=False)
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


DEAL_STAGES = (
    "discovery",
    "proposal_sent",
    "deposit_paid",
    "scheduled",
    "delivered",
    "closed_lost",
)


class Deal(db.Model):
    __tablename__ = "deals"

    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=False)
    package = db.Column(db.String(60), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    deposit_paid = db.Column(db.Boolean, nullable=False, default=False)
    deposit_paid_at = db.Column(db.DateTime(timezone=True))
    balance_due_cents = db.Column(db.Integer, nullable=False, default=0)
    balance_paid = db.Column(db.Boolean, nullable=False, default=False)
    balance_paid_at = db.Column(db.DateTime(timezone=True))  # Phase 5 (ops/digest.py cash-collected metric); mirrors deposit_paid_at. routes/webhooks.py's _handle_deal_checkout sets balance_paid but not yet this timestamp -- see ops/INTEGRATION.md.
    delivery_date = db.Column(db.Date)
    stage = db.Column(db.String(30), nullable=False, default="discovery")

    proposal_pdf_data = db.Column(db.LargeBinary)
    proposal_context = db.Column(db.JSON)  # inputs used to render the proposal, so signing can re-render it with a signature block appended without re-hitting Stripe for a new deposit link
    signed_pdf_data = db.Column(db.LargeBinary)
    signed_at = db.Column(db.DateTime(timezone=True))
    deposit_stripe_session_id = db.Column(db.String(200))
    balance_stripe_session_id = db.Column(db.String(200))
    balance_invoice_sent_at = db.Column(db.DateTime(timezone=True))
    balance_reminder_sent_at = db.Column(db.DateTime(timezone=True))

    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    sessions = db.relationship("EncounterSession", backref="deal", lazy=True)
    signature_requests = db.relationship("SignatureRequest", backref="deal", lazy=True)

    def to_dict(self):
        return {
            "id": self.id,
            "lead_id": self.lead_id,
            "package": self.package,
            "amount_cents": self.amount_cents,
            "deposit_paid": self.deposit_paid,
            "balance_due_cents": self.balance_due_cents,
            "balance_paid": self.balance_paid,
            "delivery_date": self.delivery_date.isoformat() if self.delivery_date else None,
            "stage": self.stage,
        }


SIGNATURE_STATUSES = ("pending", "signed", "declined")


class SignatureRequest(db.Model):
    """Native e-signature — token-based sign link + canvas capture, modeled
    on the Hub's own hub/esign.py rather than a third-party e-sign vendor."""
    __tablename__ = "signature_requests"

    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey("deals.id"), nullable=False)
    token = db.Column(db.String(64), nullable=False, unique=True, index=True)
    signer_name = db.Column(db.String(200), nullable=False)
    signer_email = db.Column(db.String(320), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="pending")
    typed_name = db.Column(db.String(200))
    signature_png = db.Column(db.LargeBinary)
    signer_ip = db.Column(db.String(64))
    signed_at = db.Column(db.DateTime(timezone=True))
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


PACKAGES = {
    "rpsas_taste": {"label": "RPSAS Taste", "amount_cents": 150000, "track": None},
    "applicant_cohort_seat": {"label": "Applicant Cohort Seat", "amount_cents": 180000, "track": "applicant"},
    "match_ready": {"label": "Match Ready", "amount_cents": 1000000, "track": "applicant"},
    "twenty_hour_block": {"label": "20-Hour Block", "amount_cents": 700000, "track": "physician"},
    "physician_private": {"label": "Physician Private", "amount_cents": 1250000, "track": "physician"},
    "program_cohort_1day": {"label": "Program Cohort (1-Day)", "amount_cents": 1800000, "track": "program"},
    "program_cohort_2day": {"label": "Program Cohort (2-Day)", "amount_cents": 3400000, "track": "program"},
    "system_series": {"label": "System Series", "amount_cents": 9000000, "track": "program"},
}


class EncounterSession(db.Model):
    __tablename__ = "sessions"

    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey("deals.id"), nullable=False)
    date = db.Column(db.Date)
    type = db.Column(db.String(60))  # e.g. "intensive_day_1", "check_in_30day"
    participants = db.Column(db.JSON, nullable=False, default=list)  # [{name, email}, ...]
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    scorecards = db.relationship("Scorecard", backref="session", lazy=True)


class Scorecard(db.Model):
    __tablename__ = "scorecards"

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("sessions.id"), nullable=False)
    participant_name = db.Column(db.String(200), nullable=False)
    participant_email = db.Column(db.String(320))
    baseline = db.Column(db.JSON)  # {"read_accuracy":3, ...}
    final = db.Column(db.JSON)
    lift = db.Column(db.JSON)  # computed via shared.rubric.score_lift, cached here
    clip_timestamps = db.Column(db.JSON, default=list)
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class Sequence(db.Model):
    __tablename__ = "sequences"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    track = db.Column(db.String(20))  # applicant | physician | program | None=all
    active = db.Column(db.Boolean, nullable=False, default=True)
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    steps = db.relationship(
        "SequenceStep", backref="sequence", lazy=True, order_by="SequenceStep.step_order"
    )


class SequenceStep(db.Model):
    __tablename__ = "sequence_steps"

    id = db.Column(db.Integer, primary_key=True)
    sequence_id = db.Column(db.Integer, db.ForeignKey("sequences.id"), nullable=False)
    step_order = db.Column(db.Integer, nullable=False)
    delay_days = db.Column(db.Integer, nullable=False, default=0)
    channel = db.Column(db.String(20), nullable=False, default="email")  # email | sms
    subject = db.Column(db.String(300))
    body = db.Column(db.Text, nullable=False)


ENROLLMENT_STATUSES = ("active", "stopped", "completed")


class SequenceEnrollment(db.Model):
    __tablename__ = "sequence_enrollments"

    id = db.Column(db.Integer, primary_key=True)
    sequence_id = db.Column(db.Integer, db.ForeignKey("sequences.id"), nullable=False)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=False)
    current_step_index = db.Column(db.Integer, nullable=False, default=0)
    next_fire_at = db.Column(db.DateTime(timezone=True))
    status = db.Column(db.String(20), nullable=False, default="active")
    stop_reason = db.Column(db.String(60))
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    sequence = db.relationship("Sequence")


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"))
    direction = db.Column(db.String(10), nullable=False)  # outbound | inbound
    channel = db.Column(db.String(20), nullable=False, default="email")
    thread_id = db.Column(db.String(200))
    subject = db.Column(db.String(300))
    body = db.Column(db.Text)
    sent_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    replied = db.Column(db.Boolean, nullable=False, default=False)


class ContentItem(db.Model):
    __tablename__ = "content_items"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(300))
    type = db.Column(db.String(30))  # clip | linkedin_post | newsletter | caption | proof_snippet
    status = db.Column(db.String(20), nullable=False, default="draft")  # draft | approved | scheduled | published
    body = db.Column(db.Text)
    item_metadata = db.Column(db.JSON, default=dict)
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    assets = db.relationship("Asset", backref="content_item", lazy=True)


class Asset(db.Model):
    __tablename__ = "assets"

    id = db.Column(db.Integer, primary_key=True)
    content_item_id = db.Column(db.Integer, db.ForeignKey("content_items.id"))
    kind = db.Column(db.String(30))  # video | image | pdf
    path = db.Column(db.String(500))
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


# ── RPSAS Practice (the training platform) ──────────────────────────────

MICRO_LESSONS = ("read", "pick", "speak", "ask", "shift")  # unlock in this order


class PracticeUser(db.Model):
    __tablename__ = "practice_users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(320), nullable=False, unique=True, index=True)
    name = db.Column(db.String(200))
    track = db.Column(db.String(20), nullable=False)  # applicant | physician | program
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"))

    stripe_customer_id = db.Column(db.String(200))
    stripe_subscription_id = db.Column(db.String(200))
    subscription_status = db.Column(db.String(30))  # trialing | active | past_due | canceled | None
    trial_ends_at = db.Column(db.DateTime(timezone=True))

    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    sessions = db.relationship("PracticeSession", backref="user", lazy=True)
    lesson_progress = db.relationship("MicroLessonProgress", backref="user", lazy=True)

    def has_access(self):
        if self.subscription_status == "active":
            return True
        if self.trial_ends_at and utcnow() <= _aware(self.trial_ends_at):
            return True
        return False

    def to_dict(self):
        return {
            "id": self.id, "email": self.email, "name": self.name, "track": self.track,
            "subscription_status": self.subscription_status,
            "trial_ends_at": self.trial_ends_at.isoformat() if self.trial_ends_at else None,
            "has_access": self.has_access(),
        }


class MagicLinkToken(db.Model):
    __tablename__ = "magic_link_tokens"

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), nullable=False, unique=True, index=True)
    email = db.Column(db.String(320), nullable=False)
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True))


SESSION_STATUSES = ("in_progress", "completed")


class PracticeSession(db.Model):
    __tablename__ = "practice_sessions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("practice_users.id"), nullable=False)
    scenario_key = db.Column(db.String(60), nullable=False)
    initial_mode = db.Column(db.String(20), nullable=False)
    shift_after_turn = db.Column(db.Integer, nullable=False)
    shift_to_mode = db.Column(db.String(20))  # set once the shift has actually occurred
    transcript = db.Column(db.JSON, nullable=False, default=list)  # [{"role": "patient"|"trainee", "text": ...}]
    status = db.Column(db.String(20), nullable=False, default="in_progress")

    scores = db.Column(db.JSON)  # {"read_accuracy": 4, ...}
    quotes = db.Column(db.JSON)  # {"read_accuracy": "...", ...}
    named_p_initial = db.Column(db.String(20))
    named_p_after_shift = db.Column(db.String(20))
    shift_caught = db.Column(db.Boolean)
    drill = db.Column(db.Text)

    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at = db.Column(db.DateTime(timezone=True))

    def total_score(self):
        return sum(self.scores.values()) if self.scores else None


class MicroLessonProgress(db.Model):
    __tablename__ = "micro_lesson_progress"
    __table_args__ = (db.UniqueConstraint("user_id", "lesson_key"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("practice_users.id"), nullable=False)
    lesson_key = db.Column(db.String(20), nullable=False)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


# ── RPSAS Outreach (Phase 2: list builders + sequence engine) ──────────

class SuppressedEmail(db.Model):
    """Bounce/unsubscribe suppression list, checked before every enrollment
    and every builder-side dedup pass. Deliberately minimal per the brief."""
    __tablename__ = "suppressed_emails"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(320), nullable=False, unique=True, index=True)
    reason = db.Column(db.String(60), nullable=False, default="bounced")  # bounced | unsubscribed | manual
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class MessageDraft(db.Model):
    """A classifier-proposed reply to an inbound Message, shown in the admin
    inbox for one-click approve/book/dismiss."""
    __tablename__ = "message_drafts"

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("messages.id"), nullable=False)
    positive = db.Column(db.Boolean, nullable=False, default=False)
    confidence = db.Column(db.Float, nullable=False, default=0.0)
    draft_body = db.Column(db.Text)
    proposed_slots = db.Column(db.JSON, default=list)  # list of ISO datetime strings
    approved = db.Column(db.Boolean, nullable=False, default=False)
    dismissed = db.Column(db.Boolean, nullable=False, default=False)
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    message = db.relationship("Message", backref=db.backref("draft", uselist=False))
