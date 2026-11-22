from functools import wraps
from typing import Optional

import bcrypt
from flask import flash, jsonify, redirect, request, session, url_for
from sqlalchemy import func, or_, select

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

    @classmethod
    def resolve_user_role_and_profile(cls, user: User) -> tuple[str, Optional[str], str]:
        """
        Resolve (role, profile_id, fullname) for a User by checking role tables
        (Admins, Lecturers, Students) or direct model attributes.
        """
        uid = getattr(user, "user_id", getattr(user, "id", None))
        default_name = getattr(user, "fullname", None) or getattr(user, "username", "")

        if isinstance(user, Admin):
            return "admin", getattr(user, "admin_id", None), default_name
        if isinstance(user, Lecturer):
            return "lecturer", getattr(user, "lecturer_id", None), getattr(user, "fullname", default_name)
        if isinstance(user, Student):
            return "student", getattr(user, "student_id", None), getattr(user, "fullname", default_name)

        if uid is not None:
            try:
                admin_row = db.session.execute(
                    select(Admin.__table__.c.adminid).where(Admin.__table__.c.userid == uid)
                ).first()
                if admin_row:
                    return "admin", admin_row[0], default_name

                lecturer_row = db.session.execute(
                    select(Lecturer.__table__.c.lecturerid, Lecturer.__table__.c.fullname).where(
                        Lecturer.__table__.c.userid == uid
                    )
                ).first()
                if lecturer_row:
                    return "lecturer", lecturer_row[0], lecturer_row[1] or default_name

                student_row = db.session.execute(
                    select(Student.__table__.c.studentid, Student.__table__.c.fullname).where(
                        Student.__table__.c.userid == uid
                    )
                ).first()
                if student_row:
                    return "student", student_row[0], student_row[1] or default_name
            except Exception:
                pass

        raw_role = (getattr(user, "role", "") or "").strip().lower()
        if raw_role in ("admin", "administrator"):
            return "admin", getattr(user, "admin_id", None), default_name
        if raw_role in ("lecturer", "student"):
            return raw_role, None, default_name

        return "", None, default_name

    @classmethod
    def get_profile_id(cls, user: User) -> Optional[str]:
        """Resolve role-specific profile ID if a matching profile record exists."""
        _, profile_id, _ = cls.resolve_user_role_and_profile(user)
        return profile_id

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

        role, profile_id, fullname = cls.resolve_user_role_and_profile(user)
        uid = getattr(user, "user_id", getattr(user, "id", None))
        session_data = {
            "user_id": uid,
            "username": user.username,
            "email": user.email,
            "fullname": fullname,
            "role": role,
            "profile_id": profile_id,
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