import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # for `shared.*`

from flask import Flask

from config import Config
from extensions import db


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)

    db.init_app(app)

    from routes.public import bp as public_bp
    from routes.admin import bp as admin_bp
    from routes.webhooks import bp as webhooks_bp
    from routes.esign import bp as esign_bp
    from routes.practice import bp as practice_bp
    from routes.content_admin import bp as content_admin_bp
    from routes.inbox import bp as inbox_bp
    from routes.unsubscribe import bp as unsubscribe_bp
    from routes.playbook import bp as playbook_bp
    from routes.closers import bp as closers_bp
    from routes.qualifier import bp as qualifier_bp
    from routes.call_intake import bp as call_intake_bp
    from routes.delivery import bp as delivery_bp
    from routes.intake import bp as intake_bp
    from routes.uploads import bp as uploads_bp
    from routes.consent import bp as consent_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(webhooks_bp)
    app.register_blueprint(esign_bp)
    app.register_blueprint(practice_bp)
    app.register_blueprint(content_admin_bp)
    app.register_blueprint(inbox_bp)
    app.register_blueprint(unsubscribe_bp)
    app.register_blueprint(playbook_bp)
    app.register_blueprint(closers_bp)
    app.register_blueprint(qualifier_bp)
    app.register_blueprint(call_intake_bp)
    app.register_blueprint(delivery_bp)
    app.register_blueprint(intake_bp)
    app.register_blueprint(uploads_bp)
    app.register_blueprint(consent_bp)

    register_cli(app)

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "brand": app.config["BRAND_NAME"]}

    return app


def register_cli(app):
    @app.cli.command("init-db")
    def init_db():
        """Creates all tables. Run once per environment: `flask init-db`."""
        with app.app_context():
            db.create_all()
        print("Database initialized.")

    @app.cli.command("google-auth")
    def google_auth():
        """Interactive one-time OAuth flow for the Gmail/Calendar sending
        account. Prints the token JSON to store as GOOGLE_TOKEN_JSON."""
        from services.google_auth import run_local_oauth_flow

        with app.app_context():
            print(run_local_oauth_flow())

    @app.cli.command("seed-sequences")
    def seed_sequences():
        """Seeds the four-touch nurture sequences described in the build
        brief (Phase 2 runs these; the schema is created in Phase 1)."""
        from models import Sequence, SequenceStep

        with app.app_context():
            if Sequence.query.count() > 0:
                print("Sequences already seeded.")
                return
            for track in (None, "applicant", "physician", "program"):
                seq = Sequence(name=f"Nurture — {track or 'all tracks'}", track=track)
                db.session.add(seq)
                db.session.flush()
                steps = [
                    (0, "Demo clip + one line"),
                    (4, "One-page proof"),
                    (8, "Invitation to a free 45-minute lunch-hour talk"),
                    (12, "Break-up"),
                ]
                for i, (delay, label) in enumerate(steps):
                    db.session.add(SequenceStep(
                        sequence_id=seq.id, step_order=i, delay_days=delay,
                        channel="email", subject=label, body=f"[TODO: {label} copy]",
                    ))
            db.session.commit()
            print("Seeded nurture sequences.")

    @app.cli.command("send-digest")
    def send_digest_cmd():
        """Sends the Monday weekly ops digest to FOUNDER_EMAIL. See
        ops/digest.py. Scheduled via render.yaml (see ops/INTEGRATION.md)."""
        from ops.digest import send_digest

        with app.app_context():
            try:
                result = send_digest()
                print(f"Digest sent: {result}")
            except Exception as e:
                try:
                    from ops.error_alerts import alert
                    alert("Weekly digest failed to send", str(e))
                except Exception:
                    pass  # don't let a broken alert path mask the original failure
                raise

    @app.cli.command("backup-db")
    def backup_db_cmd():
        """Nightly DB backup: dump -> gzip -> GitHub release or S3. See
        ops/backup.py. Scheduled via render.yaml (see ops/INTEGRATION.md)."""
        from ops.backup import run_backup

        with app.app_context():
            try:
                result = run_backup()
                print(f"Backup uploaded: {result}")
            except Exception as e:
                try:
                    from ops.error_alerts import alert
                    alert("Nightly DB backup failed", str(e))
                except Exception:
                    pass
                raise

    @app.cli.command("tick-sequences")
    def tick_sequences_cmd():
        """Advances due sequence enrollments (sends the next step). See
        outreach/engine/sequences.py. Scheduled every 15 min via render.yaml."""
        from outreach.engine import sequences

        with app.app_context():
            try:
                result = sequences.tick()
                print(f"Sequence tick: {result}")
            except Exception as e:
                try:
                    from ops.error_alerts import alert
                    alert("Sequence tick failed", str(e))
                except Exception:
                    pass
                raise

    @app.cli.command("auto-approve-proposals")
    def auto_approve_proposals_cmd():
        """Phase 6 call-to-proposal safety net: approves any Deal still
        `proposal_pending_review` more than 2 hours after
        `proposal_pending_since` (the founder's one-click approval window).
        See services/call_to_proposal.py::sweep_auto_approve() and
        api/INTEGRATION.md for the render.yaml cron entry."""
        from services.call_to_proposal import sweep_auto_approve

        with app.app_context():
            try:
                result = sweep_auto_approve()
                print(f"Auto-approved proposals: {result}")
            except Exception as e:
                try:
                    from ops.error_alerts import alert
                    alert("Proposal auto-approve sweep failed", str(e))
                except Exception:
                    pass
                raise

    @app.cli.command("check-content-inventory")
    def check_content_inventory_cmd():
        """Phase 6 content-on-inventory: estimates days of scheduled
        LinkedIn content remaining and alerts FOUNDER_EMAIL (once per
        drop-below-30-days event) if it's running low. See
        services/content_inventory.py::check_inventory_and_alert() and this
        repo's top-level INTEGRATION.md for the render.yaml cron entry."""
        from services.content_inventory import check_inventory_and_alert

        with app.app_context():
            try:
                result = check_inventory_and_alert()
                print(f"Content inventory check: {result}")
            except Exception as e:
                try:
                    from ops.error_alerts import alert
                    alert("Content inventory check failed", str(e))
                except Exception:
                    pass
                raise

    @app.cli.command("send-delivery-reminders")
    def send_delivery_reminders_cmd():
        """Sends the 7/3/1-day-before-delivery reminder chain for deals with
        a delivery_date. See services/onboarding.py::send_delivery_reminders.
        Scheduled daily via render.yaml (see api/INTEGRATION.md)."""
        from services.onboarding import send_delivery_reminders

        with app.app_context():
            try:
                result = send_delivery_reminders()
                print(f"Delivery reminders: {result}")
            except Exception as e:
                try:
                    from ops.error_alerts import alert
                    alert("Delivery reminder sweep failed", str(e))
                except Exception:
                    pass
                raise

    @app.cli.command("send-scheduled-followups")
    def send_scheduled_followups_cmd():
        """Sends whatever's due from the post-delivery followup schedule
        (30-day check-in confirmation, day-7 referral ask). See
        services/delivery.py::send_scheduled_followups. Scheduled daily via
        render.yaml (see api/INTEGRATION.md)."""
        from services.delivery import send_scheduled_followups

        with app.app_context():
            try:
                result = send_scheduled_followups()
                print(f"Scheduled followups: {result}")
            except Exception as e:
                try:
                    from ops.error_alerts import alert
                    alert("Scheduled followup sweep failed", str(e))
                except Exception:
                    pass
                raise

    @app.cli.command("check-replies")
    def check_replies_cmd():
        """Polls Gmail threads for replies, stops enrollments, and classifies
        positive replies into the admin inbox. See
        outreach/engine/reply_detection.py. Scheduled every 15 min via render.yaml."""
        from outreach.engine import reply_detection

        with app.app_context():
            try:
                result = reply_detection.check_for_replies()
                print(f"Reply check: {result}")
            except Exception as e:
                try:
                    from ops.error_alerts import alert
                    alert("Reply detection failed", str(e))
                except Exception:
                    pass
                raise


if __name__ == "__main__":
    create_app().run(debug=True)
