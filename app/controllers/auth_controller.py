import secrets
import string
import time
from typing import Optional

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from app.extensions.database import db
from app.models import User
from app.services.auth_factory import AuthFactory

auth_bp = Blueprint("auth", __name__)

# Brute-force protection: max 5 failed attempts per 5 minutes
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 300
_failed_attempts: dict[str, list[float]] = {}


def _wants_json() -> bool:
    return request.is_json or request.accept_mimetypes.best == "application/json"


def _respond(
    success: bool, message: str, status: int = 200, template: Optional[str] = None,
    redirect_to: Optional[str] = None, category: str = "info", extra: Optional[dict] = None,
):
    """Return JSON or HTML response depending on client request."""
    if _wants_json():
        return jsonify({"success": success, "message": message, **(extra or {})}), status
    if message:
        flash(message, category)
    return redirect(redirect_to) if redirect_to else (render_template(template), status)


def _is_rate_limited(key: str) -> bool:
    now = time.time()
    _failed_attempts[key] = [t for t in _failed_attempts.get(key, []) if now - t < LOCKOUT_SECONDS]
    return len(_failed_attempts[key]) >= MAX_LOGIN_ATTEMPTS


def _generate_password(length: int = 10) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Authenticate user and redirect to role-specific dashboard."""
    if request.method == "GET":
        if "user_id" in session:
            return redirect(session.get("dashboard_url", "/"))
        return render_template("auth/login.html")

    data = (request.get_json(silent=True) or {}) if request.is_json else request.form
    identifier = (data.get("username") or data.get("email") or "").strip()
    password = data.get("password") or ""

    if not identifier or not password:
        return _respond(False, "Please enter both username/email and password.", 400, "auth/login.html", category="danger")

    rate_key = f"{request.remote_addr}:{identifier.lower()}"
    if _is_rate_limited(rate_key):
        return _respond(False, "Too many failed login attempts. Try again in 5 minutes.", 429, "auth/login.html", category="danger")

    session_data, error, status = AuthFactory.authenticate(identifier, password)
    if error:
        if status == 401:
            _failed_attempts.setdefault(rate_key, []).append(time.time())
        return _respond(False, error, status, "auth/login.html", category="danger")

    _failed_attempts.pop(rate_key, None)
    session.clear()
    session.update(session_data)

    return _respond(
        True, "Login successful.", 200,
        redirect_to=session_data["dashboard_url"],
        category="success",
        extra={"user": session_data, "redirect_url": session_data["dashboard_url"]},
    )


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    """Clear active user session and return to login screen."""
    user_id = session.get("user_id")
    if user_id:
        user = db.session.get(User, user_id)
        if user:
            user.logout()

    session.clear()
    return _respond(True, "You have been logged out.", 200, redirect_to=url_for("auth.login"))


@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    """Generate a new encrypted password and notify via email service if available."""
    if request.method == "GET":
        return render_template("auth/reset_password.html")

    data = (request.get_json(silent=True) or {}) if request.is_json else request.form
    email = (data.get("email") or "").strip()
    if not email:
        return _respond(False, "Please enter your registered email address.", 400, "auth/reset_password.html", category="danger")

    user = User.query.filter(db.func.lower(User.email) == email.lower()).first()
    if not user:
        return _respond(False, "Email address does not exist in the system.", 404, "auth/reset_password.html", category="danger")

    new_password = _generate_password()
    user.reset_password(new_password)
    db.session.commit()

    # Delegate email delivery to email_service once implemented
    email_sent = False
    try:
        from app.services import email_service
        if hasattr(email_service, "send_password_reset_email"):
            email_sent = bool(email_service.send_password_reset_email(user.email, new_password))
    except Exception:
        email_sent = False

    msg = "A new password has been sent to your registered email." if email_sent else "Password has been reset successfully."
    return _respond(True, msg, 200, redirect_to=url_for("auth.login"), category="success", extra={"email_sent": email_sent})