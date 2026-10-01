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

    # Unguessable public identifier for the pre-call qualifier flow (routes/qualifier.py).
    # Set once, when a lead first qualifies — every qualifier route resolves the lead by
    # this token instead of the sequential `id`, so the chat/booking flow can't be hijacked
    # by walking lead ids (see api/routes/qualifier.py for the routes this gates).
    qualifier_token = db.Column(db.String(64), unique=True, index=True)

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

    # Phase 6: pre-call qualifier (services/qualifier_chat.py) writes its
    # one-paragraph brief here once a lead clears the $10k+ bar and a Deal
    # is opened for them -- kept on the Deal (rather than only on
    # QualifierSession) so it's visible wherever a Deal already is, with no
    # extra join.
    qualifier_brief = db.Column(db.Text)

    # Phase 6: call-to-proposal (services/call_to_proposal.py). Set True the
    # moment the extraction pipeline generates a proposal PDF + e-sign
    # request + deposit link from a call transcript; cleared by either the
    # founder's one-click approval (routes/call_intake.py) or the
    # `flask auto-approve-proposals` 2-hour sweep. Nothing downstream (the
    # sign link, the deposit link) is emailed/activated while this is True --
    # `stage` only flips to "proposal_sent" at approval time, same moment
    # admin.py's generate_proposal() flips it today.
    proposal_pending_review = db.Column(db.Boolean, nullable=False, default=False)
    proposal_pending_since = db.Column(db.DateTime(timezone=True))

    # ── Phase 6: onboarding automation (services/onboarding.py) ──────────
    onboarding_triggered_at = db.Column(db.DateTime(timezone=True))  # idempotency guard for trigger_onboarding()
    # Separate idempotency guard, deliberately decoupled from onboarding_triggered_at:
    # a deal can reach trigger_onboarding() with delivery_date still unknown (a call
    # transcript that didn't state one, or a self-serve package checkout with no
    # discovery call at all) — trigger_onboarding only runs once, so without a
    # separate flag, calendar holds set to None at that moment would be skipped
    # forever even after someone fills in delivery_date later. See
    # services.onboarding.ensure_calendar_holds().
    calendar_holds_booked_at = db.Column(db.DateTime(timezone=True))
    reminder_7d_sent = db.Column(db.Boolean, nullable=False, default=False)
    reminder_3d_sent = db.Column(db.Boolean, nullable=False, default=False)
    reminder_1d_sent = db.Column(db.Boolean, nullable=False, default=False)

    # ── Phase 6: post-delivery automation (routes/delivery.py) ───────────
    delivered_at = db.Column(db.DateTime(timezone=True))  # set once "mark session complete" has run (idempotency guard)

    # ── Phase 6: sales handoff readiness (services/closers.py) ───────────
    # closer_id null = founder-owned. commission_cents is only ever computed
    # once the deal actually reaches a stage that represents a real close
    # (see services/closers.py::sync_commission) -- never set speculatively
    # ahead of that, so it never overstates what a closer is owed.
    closer_id = db.Column(db.Integer, db.ForeignKey("closers.id"))
    commission_cents = db.Column(db.Integer)

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


QUALIFIER_STATUSES = ("in_progress", "completed")


class QualifierSession(db.Model):
    """Phase 6 pre-call qualifier: a short Claude-driven chat (see
    services/qualifier_chat.py) that runs after a qualified application,
    before anyone is allowed to book a call with the founder. Covers
    situation/timeline/budget/decision-maker/objections, then writes a
    one-paragraph brief and a $10k+ budget-fit verdict. A True verdict opens
    (or reuses) a Deal at stage "discovery" and unlocks the booking routes
    in routes/qualifier.py; a False verdict routes the lead to self-serve
    checkout for `recommended_package` instead -- the founder never sees a
    sub-$10k call."""
    __tablename__ = "qualifier_sessions"

    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=False)
    transcript = db.Column(db.JSON, nullable=False, default=list)  # [{"role": "lead"|"assistant", "text": ...}]
    status = db.Column(db.String(20), nullable=False, default="in_progress")  # in_progress | completed
    brief = db.Column(db.Text)
    budget_fit = db.Column(db.Boolean)  # True = $10k+ fit, book a call. False = route to self-serve.
    recommended_package = db.Column(db.String(60))  # a sub-$10k PACKAGES key, set only when budget_fit is False
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at = db.Column(db.DateTime(timezone=True))

    lead = db.relationship("Lead", backref="qualifier_sessions")


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


# ── RPSAS Founder-off-the-loop automation (Phase 6) ─────────────────────
# Onboarding (services/onboarding.py) and post-delivery (routes/delivery.py)
# automation. See api/INTEGRATION.md for the exact wiring gaps this leaves
# in files this change deliberately avoided touching.

class IntakeForm(db.Model):
    """One per deal, created by services/onboarding.py::trigger_onboarding().
    Public form at GET/POST /intake/<token> (routes/intake.py)."""
    __tablename__ = "intake_forms"

    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey("deals.id"), nullable=False)
    token = db.Column(db.String(64), nullable=False, unique=True, index=True)
    responses = db.Column(db.JSON)  # {"emergency_contact":..., "sizing":..., "accessibility":..., "special_requests":...}
    submitted_at = db.Column(db.DateTime(timezone=True))
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    deal = db.relationship("Deal", backref="intake_forms")


class UploadLink(db.Model):
    """Per-participant baseline-video upload link. Local disk storage today
    (UPLOAD_STORAGE_DIR) -- see api/INTEGRATION.md for the S3 swap-in note.
    Public form at GET/POST /upload/<token> (routes/uploads.py)."""
    __tablename__ = "upload_links"

    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey("deals.id"), nullable=False)
    participant_name = db.Column(db.String(200), nullable=False)
    participant_email = db.Column(db.String(320))
    token = db.Column(db.String(64), nullable=False, unique=True, index=True)
    uploaded_at = db.Column(db.DateTime(timezone=True))
    file_path = db.Column(db.String(500))
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    deal = db.relationship("Deal", backref="upload_links")


class CohortRoster(db.Model):
    """program-track only: sponsor-facing participant roster + room/AV
    checklist. Public form at GET/POST /roster/<token> (routes/intake.py)."""
    __tablename__ = "cohort_rosters"

    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey("deals.id"), nullable=False)
    token = db.Column(db.String(64), nullable=False, unique=True, index=True)
    participants = db.Column(db.JSON, default=list)  # [{"name":..., "email":...}, ...]
    av_checklist = db.Column(db.JSON, default=dict)  # {"projector": true, "whiteboard": false, ...}
    submitted_at = db.Column(db.DateTime(timezone=True))
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    deal = db.relationship("Deal", backref="cohort_rosters")


CONSENT_KINDS = ("testimonial", "clip_consent")


class ConsentRequest(db.Model):
    """A testimonial or clip-consent ask sent after a scorecard is delivered.
    Public respond form at GET/POST /consent/<token> (routes/consent.py).
    `approved` gates whether a granted testimonial shows on the public Proof
    page -- reviewed/approved on that deal's /admin/delivery/<id> page
    (routes/delivery.py::approve_testimonial), queried by
    routes/public.py::public_proof."""
    __tablename__ = "consent_requests"

    id = db.Column(db.Integer, primary_key=True)
    scorecard_id = db.Column(db.Integer, db.ForeignKey("scorecards.id"))
    session_id = db.Column(db.Integer, db.ForeignKey("sessions.id"))
    kind = db.Column(db.String(20), nullable=False)  # testimonial | clip_consent
    participant_name = db.Column(db.String(200))
    participant_email = db.Column(db.String(320))
    token = db.Column(db.String(64), nullable=False, unique=True, index=True)
    responded_at = db.Column(db.DateTime(timezone=True))
    response_text = db.Column(db.Text)
    granted = db.Column(db.Boolean)
    approved = db.Column(db.Boolean, nullable=False, default=False)  # founder approval -> Proof page
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    scorecard = db.relationship("Scorecard", backref="consent_requests")
    session = db.relationship("EncounterSession", backref="consent_requests")


FOLLOWUP_KINDS = ("checkin_30day", "referral_ask")


class ScheduledFollowup(db.Model):
    """A due-date row swept by `flask send-scheduled-followups`
    (services/onboarding.py::send_scheduled_followups). Created by
    routes/delivery.py's complete() action for each participant's lead."""
    __tablename__ = "scheduled_followups"

    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey("deals.id"), nullable=False)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=False)
    kind = db.Column(db.String(30), nullable=False)  # checkin_30day | referral_ask
    due_at = db.Column(db.DateTime(timezone=True), nullable=False)
    sent_at = db.Column(db.DateTime(timezone=True))
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    deal = db.relationship("Deal", backref="scheduled_followups")
    lead = db.relationship("Lead")


class ReferralClick(db.Model):
    """Records a click on a participant's /refer/<lead_id> shareable link.
    No attribution/tracking beyond "someone clicked" per the brief's scope."""
    __tablename__ = "referral_clicks"

    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=False)
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    lead = db.relationship("Lead")


# ── RPSAS Founder-off-the-loop automation (Phase 6, cont'd): content on
# inventory + sales handoff readiness. See api/services/content_inventory.py,
# api/services/call_playbook.py, api/services/closers.py, and this repo's
# top-level INTEGRATION.md for how these wire together.

class InventoryAlertLog(db.Model):
    """One row per content-inventory "dropped below 30 days" event, so
    services/content_inventory.py::check_inventory_and_alert() sends the
    founder exactly one alert per drop-below-threshold event rather than
    re-alerting every time the daily/weekly cron runs while inventory stays
    low. `resolved_at` is null while the drop is still "active"; it's set
    the next time inventory is observed back at/above the threshold, which
    re-arms alerting for the *next* drop."""
    __tablename__ = "inventory_alert_log"

    id = db.Column(db.Integer, primary_key=True)
    sent_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    days_remaining_at_send = db.Column(db.Float, nullable=False)
    resolved_at = db.Column(db.DateTime(timezone=True))


class CallPlaybook(db.Model):
    """One row per `services/call_playbook.py::generate_playbook()` run.
    Admin can regenerate at will (POST /admin/playbook/regenerate); the
    latest row by generated_at is what GET /admin/playbook shows."""
    __tablename__ = "call_playbooks"

    id = db.Column(db.Integer, primary_key=True)
    generated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    transcript_count = db.Column(db.Integer, nullable=False, default=0)
    objections = db.Column(db.JSON, nullable=False, default=list)  # [{"objection": str, "response": str}, ...]
    close_lines = db.Column(db.JSON, nullable=False, default=list)  # [str, ...]


class Closer(db.Model):
    """A delegated closer for $10k+ deals -- own calendar (its assigned
    Deals' delivery_dates, see GET /admin/closers/<id>/calendar) and
    commission tracking (Deal.closer_id / Deal.commission_cents)."""
    __tablename__ = "closers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(320), nullable=False, unique=True, index=True)
    commission_rate = db.Column(db.Float, nullable=False, default=0.10)  # e.g. 0.10 = 10%
    active = db.Column(db.Boolean, nullable=False, default=True)
    created = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    deals = db.relationship("Deal", backref="closer", lazy=True)


class ProcessedWebhookEvent(db.Model):
    """Idempotency guard for routes/webhooks.py's Stripe handler. Stripe's
    delivery guarantee is at-least-once -- a network blip or a slow response
    on our end makes Stripe retry the same event, and without this, a
    replayed checkout.session.completed would create a second Deal (or
    double-run whatever else that event triggers)."""
    __tablename__ = "processed_webhook_events"

    id = db.Column(db.String(255), primary_key=True)  # the provider's event id, e.g. Stripe's evt_...
    processed_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
