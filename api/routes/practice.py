import functools

from flask import (
    Blueprint, current_app, g, jsonify, redirect, render_template, request, session, url_for,
)

from extensions import db
from models import MICRO_LESSONS, MicroLessonProgress, PracticeSession, PracticeUser
from services import magic_link, patient_sim, stripe_client
from shared.prompts.scenarios import SCENARIOS

bp = Blueprint("practice", __name__, url_prefix="/practice")


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get("practice_user_id")
        if not user_id:
            return redirect(url_for("practice.login_page"))
        user = db.session.get(PracticeUser, user_id)
        if not user:
            session.pop("practice_user_id", None)
            return redirect(url_for("practice.login_page"))
        g.practice_user = user
        return view(*args, **kwargs)
    return wrapped


@bp.get("/login")
def login_page():
    return render_template("practice/login.html", scenarios=SCENARIOS)


@bp.post("/login")
def request_login():
    data = request.get_json(silent=True) or request.form
    email = (data.get("email") or "").strip()
    track = data.get("track")
    name = data.get("name")
    if not email:
        return jsonify({"error": "email is required"}), 400

    existing = PracticeUser.query.filter_by(email=email.strip().lower()).first()
    if not existing and not track:
        return jsonify({"error": "track is required for a new account"}), 400

    try:
        sign_in_url, user = magic_link.request_login(email, track=track, name=name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Dev convenience: without a live Gmail sender configured, hand the link
    # straight back instead of silently doing nothing.
    dev_link = sign_in_url if not current_app.config["GMAIL_SENDER_EMAIL"] else None
    return jsonify({"sent": True, "dev_link": dev_link})


@bp.get("/auth/<token>")
def verify_login(token):
    user = magic_link.verify_and_consume(token)
    if not user:
        return render_template("practice/login.html", scenarios=SCENARIOS, error="That link is invalid or has expired."), 400
    session["practice_user_id"] = user.id
    return redirect(url_for("practice.dashboard"))


@bp.get("/logout")
def logout():
    session.pop("practice_user_id", None)
    return redirect(url_for("practice.login_page"))


@bp.get("")
@login_required
def dashboard():
    user = g.practice_user
    sessions = PracticeSession.query.filter_by(user_id=user.id).order_by(PracticeSession.created.desc()).limit(20).all()
    completed_lessons = {p.lesson_key for p in user.lesson_progress}
    next_lesson = next((k for k in MICRO_LESSONS if k not in completed_lessons), None)
    return render_template(
        "practice/dashboard.html", user=user, sessions=sessions,
        completed_lessons=completed_lessons, next_lesson=next_lesson, scenarios=SCENARIOS,
    )


@bp.post("/subscribe")
@login_required
def subscribe():
    user = g.practice_user
    track = user.track if user.track in current_app.config["PRACTICE_PRICES"] else "physician"
    success_url = f"{current_app.config['API_BASE_URL']}/practice?subscribed=1"
    cancel_url = f"{current_app.config['API_BASE_URL']}/practice?subscribed=0"
    checkout = stripe_client.create_subscription_checkout_session(user, track, success_url, cancel_url)
    return jsonify({"checkout_url": checkout["url"]})


@bp.post("/sessions")
@login_required
def create_session():
    if not current_app.config["ANTHROPIC_API_KEY"]:
        return jsonify({
            "error": "not_configured",
            "message": "Practice sessions need ANTHROPIC_API_KEY configured on the server.",
        }), 503

    user = g.practice_user
    if not user.has_access():
        return jsonify({"error": "trial_expired", "message": "Your trial has ended — subscribe to keep practicing."}), 402

    data = request.get_json(silent=True) or {}
    scenario_key = data.get("scenario_key")
    if scenario_key not in SCENARIOS:
        return jsonify({"error": "unknown scenario_key"}), 400

    mode = data.get("mode") or patient_sim.random_mode()
    opening_line = patient_sim.start_encounter(scenario_key, mode)

    practice_session = PracticeSession(
        user_id=user.id, scenario_key=scenario_key, initial_mode=mode,
        shift_after_turn=patient_sim.random_shift_turn(),
        transcript=[{"role": "patient", "text": opening_line}],
    )
    db.session.add(practice_session)
    db.session.commit()

    return jsonify({
        "session_id": practice_session.id,
        "scenario_label": SCENARIOS[scenario_key]["label"],
        "opening_line": opening_line,
    }), 201


@bp.post("/sessions/<int:session_id>/turn")
@login_required
def submit_turn(session_id):
    practice_session = PracticeSession.query.filter_by(id=session_id, user_id=g.practice_user.id).first_or_404()
    if practice_session.status != "in_progress":
        return jsonify({"error": "session already completed"}), 409

    data = request.get_json(silent=True) or {}
    trainee_text = (data.get("text") or "").strip()
    if not trainee_text:
        return jsonify({"error": "text is required"}), 400

    transcript = list(practice_session.transcript)
    transcript.append({"role": "trainee", "text": trainee_text})

    trainee_turn_count = sum(1 for t in transcript if t["role"] == "trainee")
    is_shift_turn = trainee_turn_count == practice_session.shift_after_turn and not practice_session.shift_to_mode
    shift_to_mode = patient_sim.random_mode(exclude=practice_session.initial_mode) if is_shift_turn else practice_session.shift_to_mode

    current_mode = shift_to_mode if (practice_session.shift_to_mode or is_shift_turn) else practice_session.initial_mode
    reply = patient_sim.patient_reply(
        practice_session.scenario_key, current_mode, transcript,
        is_shift_turn=is_shift_turn, shift_to_mode=shift_to_mode if is_shift_turn else None,
    )
    transcript.append({"role": "patient", "text": reply})

    practice_session.transcript = transcript
    if is_shift_turn:
        practice_session.shift_to_mode = shift_to_mode
    db.session.commit()

    return jsonify({"reply": reply, "shift_occurred": is_shift_turn})


@bp.post("/sessions/<int:session_id>/complete")
@login_required
def complete_session(session_id):
    from datetime import datetime, timezone

    practice_session = PracticeSession.query.filter_by(id=session_id, user_id=g.practice_user.id).first_or_404()
    if practice_session.status == "completed":
        return jsonify(_session_result(practice_session))

    result = patient_sim.score_encounter(
        practice_session.scenario_key, practice_session.transcript,
        practice_session.initial_mode, practice_session.shift_to_mode,
    )
    practice_session.scores = result["scores"]
    practice_session.quotes = result["quotes"]
    practice_session.named_p_initial = result["named_p_initial"]
    practice_session.named_p_after_shift = result.get("named_p_after_shift")
    practice_session.shift_caught = result["shift_caught"]
    practice_session.drill = result["drill"]
    practice_session.status = "completed"
    practice_session.completed_at = datetime.now(timezone.utc)
    db.session.commit()

    return jsonify(_session_result(practice_session))


def _session_result(practice_session):
    return {
        "session_id": practice_session.id,
        "scores": practice_session.scores,
        "total": practice_session.total_score(),
        "quotes": practice_session.quotes,
        "named_p_initial": practice_session.named_p_initial,
        "named_p_after_shift": practice_session.named_p_after_shift,
        "shift_caught": practice_session.shift_caught,
        "drill": practice_session.drill,
    }


@bp.get("/sessions/<int:session_id>")
@login_required
def session_detail(session_id):
    practice_session = PracticeSession.query.filter_by(id=session_id, user_id=g.practice_user.id).first_or_404()
    return render_template("practice/session.html", practice_session=practice_session, scenario=SCENARIOS[practice_session.scenario_key])


@bp.post("/lessons/<lesson_key>/complete")
@login_required
def complete_lesson(lesson_key):
    if lesson_key not in MICRO_LESSONS:
        return jsonify({"error": "unknown lesson"}), 404
    existing = MicroLessonProgress.query.filter_by(user_id=g.practice_user.id, lesson_key=lesson_key).first()
    if not existing:
        db.session.add(MicroLessonProgress(user_id=g.practice_user.id, lesson_key=lesson_key))
        db.session.commit()
    return jsonify({"completed": True})
