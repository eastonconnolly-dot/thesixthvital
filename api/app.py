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

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(webhooks_bp)
    app.register_blueprint(esign_bp)
    app.register_blueprint(practice_bp)

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


if __name__ == "__main__":
    create_app().run(debug=True)
