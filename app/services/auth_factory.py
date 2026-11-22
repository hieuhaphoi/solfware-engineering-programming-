from functools import wraps
from typing import Optional

import bcrypt
from flask import flash, jsonify, redirect, request, session, url_for
from sqlalchemy import func, or_

from app.extensions.database import db
from app.models import Admin, Lecturer, Student, User


class AuthFactory:
    """Centralized authentication, bcrypt hashing, and role routing service."""

    DASHBOARD_URLS = {
        "admin": "/admin",
        "lecturer": "/lecturer",
        "student": "/student",
    }

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a plaintext password using bcrypt."""
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    @staticmethod
    def verify_password(plain_password: str, stored_hash: str) -> bool:
        """Verify plaintext password against a bcrypt hash."""
        if not plain_password or not stored_hash:
            return False
        try:
            return bcrypt.checkpw(
                plain_password.encode("utf-8"),
                stored_hash.encode("utf-8"),
            )
        except ValueError:
            return False

    @classmethod
    def get_dashboard_url(cls, role: str) -> str:
        return cls.DASHBOARD_URLS.get((role or "").strip().lower(), "/")

    @staticmethod
    def get_profile_id(user: User) -> Optional[int]:
        """Resolve role-specific profile ID if a matching profile record exists."""
        role = (getattr(user, "role", "") or "").strip().lower()
        uid = getattr(user, "user_id", getattr(user, "id", None))
        model_map = {
            "admin": (Admin, "admin_id"),
            "lecturer": (Lecturer, "lecturer_id"),
            "student": (Student, "student_id"),
        }
        if role not in model_map or uid is None:
            return None

        model, pk_attr = model_map[role]
        try:
            if hasattr(model, "user_id"):
                record = model.query.filter_by(user_id=uid).first()
                if record:
                    return getattr(record, pk_attr, getattr(record, "id", uid))
        except Exception:
            return None
        return None

    @classmethod
    def authenticate(cls, identifier: str, password: str) -> tuple[Optional[dict], Optional[str], int]:
        """Authenticate user by username or email and return (session_data, error, status)."""
        ident = (identifier or "").strip().lower()
        if not ident or not password:
            return None, "Please enter both username/email and password.", 400

        user = User.query.filter(
            or_(
                func.lower(User.username) == ident,
                func.lower(User.email) == ident,
            )
        ).first()

        if not user:
            return None, "Invalid username/email or password.", 401

        status_val = str(getattr(user, "status", "ACTIVE") or "ACTIVE").strip().upper()
        if status_val != "ACTIVE":
            return None, "Your account is inactive or locked. Please contact an administrator.", 403

        stored_hash = getattr(user, "password_hash", None) or getattr(user, "password", "")
        valid = cls.verify_password(password, stored_hash)
        if not valid and hasattr(user, "check_password"):
            try:
                valid = bool(user.check_password(password))
            except Exception:
                valid = False

        if not valid:
            return None, "Invalid username/email or password.", 401

        role = (getattr(user, "role", "") or "").strip().lower()
        uid = getattr(user, "user_id", getattr(user, "id", None))
        session_data = {
            "user_id": uid,
            "username": user.username,
            "email": user.email,
            "role": role,
            "profile_id": cls.get_profile_id(user),
            "dashboard_url": cls.get_dashboard_url(role),
        }
        return session_data, None, 200


def _wants_json() -> bool:
    return request.is_json or request.accept_mimetypes.best == "application/json"


def login_required(fn):
    """Ensure user has an active session before accessing a route."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            if _wants_json():
                return jsonify({"success": False, "message": "Authentication required."}), 401
            flash("Please log in to continue.", "warning")
            return redirect(url_for("auth.login"))
        return fn(*args, **kwargs)

    return wrapper


def role_required(*allowed_roles: str):
    """Restrict route access to users with one of the specified roles."""
    normalized = {r.strip().lower() for r in allowed_roles}

    def decorator(fn):
        @wraps(fn)
        @login_required
        def wrapper(*args, **kwargs):
            user_role = (session.get("role") or "").strip().lower()
            if user_role not in normalized:
                if _wants_json():
                    return jsonify({"success": False, "message": "Access denied."}), 403
                flash("You do not have permission to access this page.", "danger")
                return redirect(session.get("dashboard_url", url_for("auth.login")))
            return fn(*args, **kwargs)

        return wrapper

    return decorator