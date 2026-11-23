from datetime import datetime, timedelta
import secrets
from typing import Any, Optional

from flask import Blueprint, current_app, flash, jsonify, redirect, request, session, url_for
from sqlalchemy.exc import SQLAlchemyError

from app.extensions.database import db
from app.extensions.websocket import realtime_hub
from app.models import AttendanceRecord, AttendanceSession, CourseSection, Enrollment, Lecturer, Student
from app.services.auth_factory import login_required, role_required
from app.services.email_service import EmailService
from app.services.session_manager import PINError, SessionManager, session_manager

attendance_bp = Blueprint("attendance", __name__, url_prefix="/attendance")

VALID_ATTENDANCE_STATUSES = AttendanceRecord.VALID_STATUSES


def _wants_json() -> bool:
    return request.is_json or request.accept_mimetypes.best == "application/json"


def _now_naive() -> datetime:
    return datetime.now()


def _default_ttl() -> int:
    try:
        return SessionManager.normalize_ttl(current_app.config.get("PIN_TTL_SECONDS", 300))
    except RuntimeError:
        return SessionManager.DEFAULT_TTL_SECONDS


def _pin_digits() -> int:
    try:
        return max(4, min(6, int(current_app.config.get("PIN_LENGTH", 6))))
    except (RuntimeError, TypeError, ValueError):
        return 6


def _is_session_open(sess: AttendanceSession) -> bool:
    if not sess or sess.end_time is None:
        return False
    now = _now_naive()
    end_dt = sess.end_time.replace(tzinfo=None) if sess.end_time.tzinfo else sess.end_time
    return end_dt > now


def _verify_session_pin(sess: AttendanceSession, pin_code: str) -> None:
    """Verify submitted PIN against Redis/cache first, falling back to DB pin_code if session is still within end_time."""
    if not _is_session_open(sess):
        raise PINError("SESSION_CLOSED", "This attendance session is closed or expired.", 0)

    normalized = (pin_code or "").strip()
    if not normalized.isdigit() or not (SessionManager.MIN_PIN_LENGTH <= len(normalized) <= SessionManager.MAX_PIN_LENGTH):
        raise PINError("INVALID_FORMAT", "PIN must be a 4-6 digit numeric code.", 0)

    cached_pin = session_manager.get_active_pin(sess.session_id)
    if cached_pin is not None:
        if secrets.compare_digest(cached_pin, normalized):
            return
        rem = session_manager.get_remaining_ttl(sess.session_id)
        raise PINError("INVALID_PIN", "Incorrect PIN code. Please check with your lecturer.", rem)

    # Fallback to database pin_code when session end_time is still in the future (e.g., seeded or restarted server)
    db_pin = (sess.pin_code or "").strip()
    if db_pin and secrets.compare_digest(db_pin, normalized):
        return

    raise PINError("INVALID_PIN", "Incorrect PIN code. Please check with your lecturer.", 0)


def _lecturer_owns_section(section: CourseSection) -> bool:
    role = (session.get("role") or "").strip().lower()
    if role == "admin":
        return True
    if role != "lecturer":
        return False
    profile_id = session.get("profile_id")
    if not profile_id and session.get("user_id") is not None:
        profile_id = db.session.execute(
            db.select(Lecturer.__table__.c.lecturerid).where(
                Lecturer.__table__.c.userid == session.get("user_id")
            )
        ).scalar_one_or_none()
    if not profile_id:
        return False
    return str(section.lecturer_id or "").strip() == str(profile_id).strip()


def _create_default_absent_records(session_id: int, section_id: str) -> int:
    """Create ABSENT (absent=True) AttendanceRecord rows for all enrolled students."""
    enrollments = Enrollment.query.filter_by(section_id=section_id).all()
    existing_student_ids = {
        str(rec.student_id)
        for rec in AttendanceRecord.query.filter_by(session_id=session_id).all()
    }

    created_count = 0
    for enr in enrollments:
        sid = str(enr.student_id)
        if sid in existing_student_ids:
            continue
        rec = AttendanceRecord(
            session_id=session_id,
            student_id=enr.student_id,
            status="ABSENT",
            absent=True,
            check_time=None,
        )
        db.session.add(rec)
        existing_student_ids.add(sid)
        created_count += 1

    return created_count


@attendance_bp.route("/sessions/open", methods=["POST"])
@role_required("lecturer", "admin")
def open_session():
    """Open an attendance session for a section, issue a PIN with TTL, and default records to ABSENT."""
    data = request.get_json(silent=True) if request.is_json else request.form
    section_id = (data.get("section_id") or "").strip()
    ttl_raw = data.get("ttl_seconds") or data.get("pin_ttl")

    if not section_id:
        if _wants_json():
            return jsonify({"success": False, "message": "Section ID is required."}), 400
        flash("Please select a course section to open an attendance session.", "warning")
        return redirect(request.referrer or url_for("lecturer.assigned_sections"))

    section = CourseSection.query.filter_by(section_id=section_id).first()
    if not section:
        if _wants_json():
            return jsonify({"success": False, "message": "Course section not found."}), 404
        flash(f"Course section '{section_id}' was not found.", "danger")
        return redirect(request.referrer or url_for("lecturer.assigned_sections"))

    if not _lecturer_owns_section(section):
        if _wants_json():
            return jsonify({"success": False, "message": "You are not assigned to this section."}), 403
        flash("You do not have permission to open a session for this section.", "danger")
        return redirect(request.referrer or url_for("lecturer.assigned_sections"))

    ttl_seconds = SessionManager.normalize_ttl(ttl_raw) if ttl_raw is not None else _default_ttl()
    now = _now_naive()

    existing_open = (
        AttendanceSession.query.filter_by(section_id=section_id)
        .order_by(AttendanceSession.session_id.desc())
        .first()
    )

    try:
        if existing_open and _is_session_open(existing_open):
            sess = existing_open
        else:
            sess = AttendanceSession(
                section_id=section_id,
                session_date=now.date(),
                pin_code="000000",
                start_time=now,
                end_time=now + timedelta(seconds=ttl_seconds),
            )
            db.session.add(sess)
            db.session.flush()

        pin_info = SessionManager.issue_pin(
            session_id=sess.session_id,
            section_id=section_id,
            ttl_seconds=ttl_seconds,
            digits=_pin_digits(),
        )
        sess.pin_code = pin_info["pin_code"]
        sess.end_time = pin_info["expires_at_dt"]

        absent_created = _create_default_absent_records(sess.session_id, section_id)
        db.session.commit()

        pin_payload = {k: v for k, v in pin_info.items() if k != "expires_at_dt"}
        realtime_hub.broadcast_attendance_event(
            "session_opened",
            session_id=sess.session_id,
            section_id=section_id,
            payload={"session": sess.to_dict(), "pin": pin_payload},
        )

        payload = {
            "success": True,
            "message": f"Attendance session opened for {section_id}. PIN {pin_info['pin_code']} is active.",
            "session": sess.to_dict(),
            "pin": pin_payload,
            "absent_records_created": absent_created,
        }
        if _wants_json():
            return jsonify(payload), 200
        flash(payload["message"], "success")
        return redirect(request.referrer or url_for("lecturer.assigned_sections"))

    except SQLAlchemyError:
        db.session.rollback()
        if _wants_json():
            return jsonify({"success": False, "message": "Database error while opening attendance session."}), 500
        flash("Could not open attendance session due to a database error.", "danger")
        return redirect(request.referrer or url_for("lecturer.assigned_sections"))


@attendance_bp.route("/sessions/<int:session_id>/pin/regenerate", methods=["POST"])
@role_required("lecturer", "admin")
def regenerate_pin(session_id: int):
    """Generate a new short-lived PIN for an active attendance session."""
    sess = AttendanceSession.query.filter_by(session_id=session_id).first()
    if not sess:
        if _wants_json():
            return jsonify({"success": False, "message": "Attendance session not found."}), 404
        flash("Attendance session not found.", "danger")
        return redirect(request.referrer or url_for("lecturer.assigned_sections"))

    section = CourseSection.query.filter_by(section_id=sess.section_id).first()
    if not section or not _lecturer_owns_section(section):
        if _wants_json():
            return jsonify({"success": False, "message": "Permission denied."}), 403
        flash("You do not have permission to manage this session.", "danger")
        return redirect(request.referrer or url_for("lecturer.assigned_sections"))

    if sess.end_time is None or sess.end_time <= _now_naive():
        if _wants_json():
            return jsonify({"success": False, "message": "Cannot generate PIN for a closed or expired session."}), 400
        flash("This attendance session is closed or expired.", "warning")
        return redirect(request.referrer or url_for("lecturer.assigned_sections"))

    data = request.get_json(silent=True) if request.is_json else request.form
    ttl_raw = data.get("ttl_seconds") or data.get("pin_ttl")
    ttl_seconds = SessionManager.normalize_ttl(ttl_raw) if ttl_raw is not None else _default_ttl()

    try:
        pin_info = SessionManager.issue_pin(
            session_id=sess.session_id,
            section_id=sess.section_id,
            ttl_seconds=ttl_seconds,
            digits=_pin_digits(),
        )
        sess.pin_code = pin_info["pin_code"]
        sess.end_time = pin_info["expires_at_dt"]
        db.session.commit()

        pin_payload = {k: v for k, v in pin_info.items() if k != "expires_at_dt"}
        realtime_hub.broadcast_attendance_event(
            "pin_regenerated",
            session_id=sess.session_id,
            section_id=sess.section_id,
            payload={"pin": pin_payload},
        )

        payload = {
            "success": True,
            "message": f"New PIN generated: {pin_info['pin_code']}",
            "session_id": sess.session_id,
            "pin": pin_payload,
        }
        if _wants_json():
            return jsonify(payload), 200
        flash(payload["message"], "info")
        return redirect(request.referrer or url_for("lecturer.assigned_sections"))

    except SQLAlchemyError:
        db.session.rollback()
        if _wants_json():
            return jsonify({"success": False, "message": "Failed to update PIN in database."}), 500
        flash("Failed to regenerate PIN.", "danger")
        return redirect(request.referrer or url_for("lecturer.assigned_sections"))


@attendance_bp.route("/sessions/<int:session_id>/close", methods=["POST"])
@role_required("lecturer", "admin")
def close_session(session_id: int):
    """Close an active attendance session and immediately revoke its PIN."""
    sess = AttendanceSession.query.filter_by(session_id=session_id).first()
    if not sess:
        if _wants_json():
            return jsonify({"success": False, "message": "Attendance session not found."}), 404
        flash("Attendance session not found.", "danger")
        return redirect(request.referrer or url_for("lecturer.assigned_sections"))

    section = CourseSection.query.filter_by(section_id=sess.section_id).first()
    if not section or not _lecturer_owns_section(section):
        if _wants_json():
            return jsonify({"success": False, "message": "Permission denied."}), 403
        flash("You do not have permission to close this session.", "danger")
        return redirect(request.referrer or url_for("lecturer.assigned_sections"))

    try:
        SessionManager.revoke_pin(sess.session_id)
        sess.end_time = _now_naive()
        db.session.commit()

        realtime_hub.broadcast_attendance_event(
            "session_closed",
            session_id=sess.session_id,
            section_id=sess.section_id,
            payload={"is_open": False, "remaining_seconds": 0},
        )

        # Automatically check and warn students in this section who exceeded absence threshold
        warning_summary = None
        try:
            warning_summary = EmailService.check_and_warn_session(sess.session_id)
        except Exception:
            warning_summary = None

        msg = f"Attendance session #{sess.session_id} has been closed."
        if _wants_json():
            return jsonify({
                "success": True,
                "message": msg,
                "session_id": sess.session_id,
                "warnings": warning_summary,
            }), 200
        flash(msg, "info")
        return redirect(request.referrer or url_for("lecturer.assigned_sections"))

    except SQLAlchemyError:
        db.session.rollback()
        if _wants_json():
            return jsonify({"success": False, "message": "Database error closing session."}), 500
        flash("Could not close attendance session.", "danger")
        return redirect(request.referrer or url_for("lecturer.assigned_sections"))


@attendance_bp.route("/checkin", methods=["POST"])
@attendance_bp.route("/check-in", methods=["POST"])
@login_required
def submit_pin_checkin():
    """Student submits a PIN code to mark attendance as PRESENT for an open session."""
    role = (session.get("role") or "").strip().lower()
    student_id = session.get("profile_id")
    if not student_id and session.get("user_id") is not None:
        student_id = db.session.execute(
            db.select(Student.__table__.c.studentid).where(
                Student.__table__.c.userid == session.get("user_id")
            )
        ).scalar_one_or_none()

    data = (request.get_json(silent=True) if request.is_json else request.form) or {}
    if role != "student" and data.get("student_id"):
        student_id = (data.get("student_id") or "").strip()

    if not student_id:
        if _wants_json():
            return jsonify({"success": False, "message": "Student profile not found in session."}), 403
        flash("Student profile not found. Please sign in again.", "danger")
        return redirect(request.referrer or "/student")

    pin_code = (data.get("pin_code") or data.get("pin") or "").strip().replace(" ", "")
    raw_session_id = data.get("session_id")
    section_id = (data.get("section_id") or "").strip()

    if not pin_code:
        if _wants_json():
            return jsonify({"success": False, "code": "INVALID_PIN", "message": "Please enter a PIN code."}), 400
        flash("Please enter the PIN code provided by your lecturer.", "warning")
        return redirect(request.referrer or "/student")

    sess: Optional[AttendanceSession] = None
    if raw_session_id:
        try:
            sess = AttendanceSession.query.filter_by(session_id=int(raw_session_id)).first()
        except (TypeError, ValueError):
            sess = None
    elif section_id:
        candidates = (
            AttendanceSession.query.filter_by(section_id=section_id)
            .order_by(AttendanceSession.session_id.desc())
            .all()
        )
        sess = next((s for s in candidates if _is_session_open(s)), None)
    else:
        # If neither session_id nor section_id was sent, locate an open session among the student's enrolled sections
        enr_tbl = Enrollment.__table__
        enrolled_sec_ids = [
            r[0]
            for r in db.session.execute(
                db.select(enr_tbl.c.sectionid).where(enr_tbl.c.studentid == student_id)
            ).all()
        ]
        if enrolled_sec_ids:
            candidates = (
                AttendanceSession.query.filter(AttendanceSession.section_id.in_(enrolled_sec_ids))
                .order_by(AttendanceSession.start_time.desc(), AttendanceSession.session_id.desc())
                .all()
            )
            open_candidates = [s for s in candidates if _is_session_open(s)]
            # Prefer the session matching the submitted PIN if multiple are open
            sess = next((s for s in open_candidates if (s.pin_code or "").strip() == pin_code), None)
            if not sess and open_candidates:
                sess = open_candidates[0]

    if not sess:
        if _wants_json():
            return jsonify({"success": False, "code": "NO_OPEN_SESSION", "message": "No active attendance session found."}), 404
        flash("There is no active attendance session open for this class.", "warning")
        return redirect(request.referrer or "/student")

    if not _is_session_open(sess):
        if _wants_json():
            return jsonify({"success": False, "code": "SESSION_CLOSED", "message": "This attendance session is closed or expired."}), 400
        flash("This attendance session is closed or expired.", "warning")
        return redirect(request.referrer or "/student")

    enrolled = Enrollment.query.filter_by(
        section_id=sess.section_id,
        student_id=student_id,
    ).first()
    if not enrolled:
        if _wants_json():
            return jsonify({"success": False, "code": "NOT_ENROLLED", "message": "You are not enrolled in this course section."}), 403
        flash("You are not enrolled in this course section.", "danger")
        return redirect(request.referrer or "/student")

    try:
        _verify_session_pin(sess, pin_code)
    except PINError as err:
        if _wants_json():
            return jsonify({"success": False, "code": err.code, "message": err.message}), 400
        flash(err.message, "danger")
        return redirect(request.referrer or "/student")

    try:
        now = _now_naive()
        record = AttendanceRecord.query.filter_by(
            session_id=sess.session_id,
            student_id=student_id,
        ).first()

        if not record:
            record = AttendanceRecord(
                session_id=sess.session_id,
                student_id=student_id,
                status="ABSENT",
                absent=True,
            )
            db.session.add(record)

        record.update_status("PRESENT", check_time=now)
        db.session.commit()

        realtime_hub.broadcast_attendance_event(
            "student_checked_in",
            session_id=sess.session_id,
            section_id=sess.section_id,
            payload={"record": record.to_dict(), "source": "PIN check-in"},
        )

        msg = f"Check-in confirmed! Marked PRESENT for {sess.section_id}."
        if _wants_json():
            return jsonify(
                {
                    "success": True,
                    "message": msg,
                    "record": record.to_dict(),
                }
            ), 200

        flash(msg, "success")
        return redirect(request.referrer or "/student")

    except SQLAlchemyError:
        db.session.rollback()
        if _wants_json():
            return jsonify({"success": False, "message": "Could not record your attendance due to a database error."}), 500
        flash("An error occurred while saving your attendance.", "danger")
        return redirect(request.referrer or "/student")


@attendance_bp.route("/records/<int:record_id>/status", methods=["POST"])
@role_required("lecturer", "admin")
def update_record_manual(record_id: int):
    """Lecturer manually updates a student's attendance status (PRESENT, LATE, EXCUSED, ABSENT)."""
    record = AttendanceRecord.query.filter_by(record_id=record_id).first()
    if not record:
        if _wants_json():
            return jsonify({"success": False, "message": "Attendance record not found."}), 404
        flash("Attendance record not found.", "danger")
        return redirect(request.referrer or url_for("lecturer.assigned_sections"))

    sess = AttendanceSession.query.filter_by(session_id=record.session_id).first()
    section = CourseSection.query.filter_by(section_id=sess.section_id).first() if sess else None
    if not section or not _lecturer_owns_section(section):
        if _wants_json():
            return jsonify({"success": False, "message": "Permission denied."}), 403
        flash("You do not have permission to modify this attendance record.", "danger")
        return redirect(request.referrer or url_for("lecturer.assigned_sections"))

    data = request.get_json(silent=True) if request.is_json else request.form
    new_status = (data.get("status") or "").strip().upper()

    if new_status not in VALID_ATTENDANCE_STATUSES:
        if _wants_json():
            return jsonify({"success": False, "message": "Invalid attendance status."}), 400
        flash("Invalid attendance status selected.", "warning")
        return redirect(request.referrer or url_for("lecturer.assigned_sections"))

    try:
        record.update_status(new_status, check_time=_now_naive() if new_status in ("PRESENT", "LATE") else None)
        db.session.commit()

        realtime_hub.broadcast_attendance_event(
            "record_status_updated",
            session_id=record.session_id,
            section_id=section.section_id,
            payload={
                "record": record.to_dict(),
                "source": "No check-in" if new_status == "ABSENT" else "Lecturer updated",
            },
        )

        if new_status == "ABSENT":
            try:
                EmailService.send_absence_warning(
                    student_id=record.student_id,
                    section_id=section.section_id,
                )
            except Exception:
                pass

        msg = f"Updated student {record.student_id} status to {new_status}."
        if _wants_json():
            return jsonify(
                {
                    "success": True,
                    "message": msg,
                    "record": record.to_dict(),
                }
            ), 200

        flash(msg, "success")
        return redirect(request.referrer or url_for("lecturer.assigned_sections"))

    except (ValueError, SQLAlchemyError):
        db.session.rollback()
        if _wants_json():
            return jsonify({"success": False, "message": "Could not update attendance status."}), 500
        flash("Could not update attendance status.", "danger")
        return redirect(request.referrer or url_for("lecturer.assigned_sections"))
