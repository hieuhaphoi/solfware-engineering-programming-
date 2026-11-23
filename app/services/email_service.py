import html
import logging
import os
import smtplib
import ssl
import threading
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional, Tuple

from flask import current_app

from app.config import Config
from app.extensions.database import db
from app.models import (
    AttendanceRecord,
    AttendanceSession,
    Course,
    CourseSection,
    Enrollment,
    Lecturer,
    Student,
    User,
)

logger = logging.getLogger(__name__)

PLACEHOLDER_VALUES = {
    "",
    "your_email@example.com",
    "your_app_password",
}


class EmailService:
    """Service xử lý gửi email cảnh báo vắng học và đặt lại mật khẩu qua SMTP."""

    # Lưu vết (student_id, section_id) -> absent_count đã gửi để tránh spam trùng lặp
    _notified_absences: Dict[Tuple[str, str], int] = {}

    @classmethod
    def _get_config(cls, key: str, default: Any = None) -> Any:
        try:
            if key in current_app.config:
                return current_app.config[key]
        except RuntimeError:
            pass
        return getattr(Config, key, os.getenv(key, default))

    @classmethod
    def _smtp_settings(cls) -> Dict[str, Any]:
        host = str(cls._get_config("SMTP_HOST", "smtp.gmail.com") or "").strip()
        try:
            port = int(cls._get_config("SMTP_PORT", 587))
        except (TypeError, ValueError):
            port = 587
        user = str(cls._get_config("SMTP_USER", "") or "").strip()
        password = str(cls._get_config("SMTP_PASSWORD", "") or "").strip()
        from_email = str(
            cls._get_config("SMTP_FROM_EMAIL", user or "noreply@westbridge.edu") or ""
        ).strip()
        return {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "from_email": from_email,
        }

    @classmethod
    def is_smtp_configured(cls) -> bool:
        cfg = cls._smtp_settings()
        return (
            bool(cfg["host"])
            and cfg["user"].lower() not in PLACEHOLDER_VALUES
            and cfg["password"] not in PLACEHOLDER_VALUES
        )

    @classmethod
    def default_threshold(cls) -> float:
        try:
            val = float(cls._get_config("ABSENCE_WARNING_THRESHOLD", 0.20))
            return max(0.0, min(1.0, val))
        except (TypeError, ValueError):
            return 0.20

    @classmethod
    def clear_notification_cache(cls) -> None:
        cls._notified_absences.clear()

    @classmethod
    def send_email(
        cls,
        to_email: str,
        subject: str,
        html_body: str,
        text_body: Optional[str] = None,
        cc: Optional[List[str]] = None,
        timeout: int = 10,
    ) -> Dict[str, Any]:
        """Gửi email HTML + plain-text qua SMTP. Tự chạy dry-run nếu chưa cấu hình SMTP."""
        recipient = (to_email or "").strip()
        if not recipient or "@" not in recipient:
            return {
                "sent": False,
                "mode": "error",
                "to": recipient,
                "subject": subject,
                "error": "Invalid recipient email address.",
            }

        cc_list = [addr.strip() for addr in (cc or []) if addr and "@" in addr and addr.strip() != recipient]
        cfg = cls._smtp_settings()

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = cfg["from_email"]
        msg["To"] = recipient
        if cc_list:
            msg["Cc"] = ", ".join(cc_list)

        plain_text = text_body or "Please view this message in an HTML-compatible email client."
        msg.attach(MIMEText(plain_text, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        if not cls.is_smtp_configured():
            logger.info("[EmailService DRY-RUN] To=%s | Subject=%s", recipient, subject)
            return {
                "sent": False,
                "mode": "dry_run",
                "to": recipient,
                "cc": cc_list,
                "subject": subject,
                "message": "SMTP credentials are not configured; email logged in dry-run mode.",
            }

        all_recipients = [recipient] + cc_list
        try:
            if cfg["port"] == 465:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=timeout, context=context) as server:
                    server.login(cfg["user"], cfg["password"])
                    server.send_message(msg, from_addr=cfg["from_email"], to_addrs=all_recipients)
            else:
                with smtplib.SMTP(cfg["host"], cfg["port"], timeout=timeout) as server:
                    server.ehlo()
                    if cfg["port"] == 587:
                        server.starttls(context=ssl.create_default_context())
                        server.ehlo()
                    server.login(cfg["user"], cfg["password"])
                    server.send_message(msg, from_addr=cfg["from_email"], to_addrs=all_recipients)

            logger.info("[EmailService SENT] To=%s | Subject=%s", recipient, subject)
            return {
                "sent": True,
                "mode": "smtp",
                "to": recipient,
                "cc": cc_list,
                "subject": subject,
            }
        except Exception as err:
            logger.exception("[EmailService ERROR] Failed to send email to %s: %s", recipient, err)
            return {
                "sent": False,
                "mode": "error",
                "to": recipient,
                "cc": cc_list,
                "subject": subject,
                "error": str(err),
            }

    @classmethod
    def get_student_absence_stats(
        cls,
        student_id: str,
        section_id: str,
        threshold: Optional[float] = None,
        max_absences: Optional[int] = None,
        min_sessions: int = 1,
    ) -> Optional[Dict[str, Any]]:
        """Thống kê số buổi vắng của 1 sinh viên trong 1 lớp học phần bằng Core query."""
        stu_tbl = Student.__table__
        usr_tbl = User.__table__
        sec_tbl = CourseSection.__table__
        crs_tbl = Course.__table__
        lec_tbl = Lecturer.__table__

        stu_row = db.session.execute(
            db.select(stu_tbl.c.studentid, stu_tbl.c.fullname, usr_tbl.c.email)
            .join(usr_tbl, stu_tbl.c.userid == usr_tbl.c.userid)
            .where(stu_tbl.c.studentid == student_id)
        ).first()
        if not stu_row:
            return None

        sec_row = db.session.execute(
            db.select(
                sec_tbl.c.sectionid,
                sec_tbl.c.courseid,
                sec_tbl.c.lecturerid,
                sec_tbl.c.sectionname,
                sec_tbl.c.semester,
                sec_tbl.c.room,
                crs_tbl.c.coursename,
            )
            .join(crs_tbl, sec_tbl.c.courseid == crs_tbl.c.courseid)
            .where(sec_tbl.c.sectionid == section_id)
        ).first()
        if not sec_row:
            return None

        lecturer_name = "Lecturer"
        lecturer_email = None
        if sec_row.lecturerid:
            lec_row = db.session.execute(
                db.select(lec_tbl.c.fullname, usr_tbl.c.email)
                .join(usr_tbl, lec_tbl.c.userid == usr_tbl.c.userid)
                .where(lec_tbl.c.lecturerid == sec_row.lecturerid)
            ).first()
            if lec_row:
                lecturer_name = lec_row.fullname or lecturer_name
                lecturer_email = lec_row.email

        session_ids = [
            r[0]
            for r in db.session.execute(
                db.select(AttendanceSession.session_id).where(AttendanceSession.section_id == section_id)
            ).all()
        ]
        total_sessions = len(session_ids)

        counts = {"PRESENT": 0, "LATE": 0, "EXCUSED": 0, "ABSENT": 0}
        if session_ids:
            rec_rows = db.session.execute(
                db.select(AttendanceRecord.status, AttendanceRecord.absent).where(
                    AttendanceRecord.student_id == student_id,
                    AttendanceRecord.session_id.in_(session_ids),
                )
            ).all()
            for status_val, is_absent in rec_rows:
                st = (status_val or ("ABSENT" if is_absent else "PRESENT")).strip().upper()
                counts[st] = counts.get(st, 0) + 1

        # Chỉ tính buổi vắng không phép (ABSENT) vào ngưỡng cảnh báo; EXCUSED không bị phạt
        absent_count = counts["ABSENT"]
        absence_rate = round(absent_count / total_sessions, 4) if total_sessions > 0 else 0.0
        eff_threshold = cls.default_threshold() if threshold is None else float(threshold)

        if max_absences is not None:
            exceeded = absent_count >= int(max_absences) and absent_count > 0
        else:
            exceeded = (
                total_sessions >= max(1, int(min_sessions))
                and absent_count > 0
                and absence_rate >= eff_threshold
            )

        return {
            "student_id": stu_row.studentid,
            "student_name": stu_row.fullname or stu_row.studentid,
            "student_email": stu_row.email,
            "section_id": sec_row.sectionid,
            "course_id": sec_row.courseid,
            "course_name": sec_row.coursename or sec_row.courseid,
            "section_name": sec_row.sectionname,
            "semester": sec_row.semester,
            "room": sec_row.room,
            "lecturer_name": lecturer_name,
            "lecturer_email": lecturer_email,
            "total_sessions": total_sessions,
            "present_count": counts["PRESENT"],
            "late_count": counts["LATE"],
            "excused_count": counts["EXCUSED"],
            "absent_count": absent_count,
            "absence_rate": absence_rate,
            "absence_percent": round(absence_rate * 100, 1),
            "threshold": eff_threshold,
            "threshold_percent": round(eff_threshold * 100, 1),
            "max_absences": max_absences,
            "exceeded": exceeded,
        }

    @classmethod
    def _build_absence_warning_content(cls, stats: Dict[str, Any]) -> Tuple[str, str, str]:
        course_label = f"{stats['course_id']} - {stats['course_name']}"
        subject = f"[Attendly] Attendance Warning: {course_label} ({stats['section_id']})"

        limit_desc = (
            f"{stats['max_absences']} unexcused absence(s)"
            if stats.get("max_absences") is not None
            else f"{stats['threshold_percent']}% absence threshold"
        )

        text_body = (
            f"Dear {stats['student_name']} ({stats['student_id']}),\n\n"
            f"This is an automated attendance warning from Attendly.\n"
            f"Course: {course_label}\n"
            f"Section: {stats['section_id']} ({stats['section_name']}) - {stats['semester']}\n"
            f"Lecturer: {stats['lecturer_name']}\n\n"
            f"Current record: {stats['absent_count']} absent out of {stats['total_sessions']} session(s) "
            f"({stats['absence_percent']}%), which has reached or exceeded the limit of {limit_desc}.\n"
            f"Present: {stats['present_count']} | Late: {stats['late_count']} | "
            f"Excused: {stats['excused_count']} | Absent: {stats['absent_count']}\n\n"
            f"Please attend upcoming classes regularly or contact your lecturer if there is any discrepancy.\n\n"
            f"Best regards,\nAttendly Attendance System"
        )

        s_name = html.escape(str(stats["student_name"]))
        s_id = html.escape(str(stats["student_id"]))
        c_label = html.escape(course_label)
        sec_info = html.escape(f"{stats['section_id']} ({stats['section_name']}) - {stats['semester']}")
        lec_name = html.escape(str(stats["lecturer_name"]))
        limit_html = html.escape(limit_desc)
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

        html_body = f"""<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; color: #1f2937; background-color: #f9fafb; padding: 20px;">
  <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border: 1px solid #e5e7eb; border-radius: 10px; overflow: hidden;">
    <div style="background: #1e293b; color: #ffffff; padding: 18px 24px;">
      <div style="font-size: 12px; letter-spacing: 0.08em; text-transform: uppercase; color: #f87171; font-weight: bold;">Attendance Warning</div>
      <h2 style="margin: 6px 0 0; font-size: 20px;">{c_label}</h2>
    </div>
    <div style="padding: 24px;">
      <p style="margin-top: 0;">Dear <strong>{s_name}</strong> ({s_id}),</p>
      <p>Your unexcused absence count in <strong>{sec_info}</strong> has reached or exceeded the allowed limit (<strong>{limit_html}</strong>).</p>
      <table style="width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 14px;">
        <tr style="background: #fef2f2;">
          <td style="padding: 10px; border: 1px solid #e5e7eb;"><strong>Unexcused Absences</strong></td>
          <td style="padding: 10px; border: 1px solid #e5e7eb; color: #dc2626;"><strong>{stats['absent_count']} / {stats['total_sessions']} session(s) ({stats['absence_percent']}%)</strong></td>
        </tr>
        <tr>
          <td style="padding: 10px; border: 1px solid #e5e7eb;">Present / Late / Excused</td>
          <td style="padding: 10px; border: 1px solid #e5e7eb;">{stats['present_count']} / {stats['late_count']} / {stats['excused_count']}</td>
        </tr>
        <tr>
          <td style="padding: 10px; border: 1px solid #e5e7eb;">Lecturer</td>
          <td style="padding: 10px; border: 1px solid #e5e7eb;">{lec_name}</td>
        </tr>
      </table>
      <p>Please make sure to attend upcoming classes on time or contact your lecturer promptly if you believe this record needs review.</p>
      <p style="font-size: 12px; color: #6b7280; margin-bottom: 0;">Sent automatically by Attendly on {generated_at}.</p>
    </div>
  </div>
</body>
</html>"""
        return subject, html_body, text_body

    @classmethod
    def send_absence_warning(
        cls,
        student_id: str,
        section_id: str,
        threshold: Optional[float] = None,
        max_absences: Optional[int] = None,
        min_sessions: int = 1,
        force: bool = False,
        notify_lecturer: bool = False,
    ) -> Dict[str, Any]:
        """Kiểm tra 1 sinh viên trong 1 lớp và gửi email cảnh báo nếu vượt ngưỡng vắng."""
        stats = cls.get_student_absence_stats(
            student_id=student_id,
            section_id=section_id,
            threshold=threshold,
            max_absences=max_absences,
            min_sessions=min_sessions,
        )
        if not stats:
            return {
                "checked": False,
                "exceeded": False,
                "sent": False,
                "reason": "student_or_section_not_found",
                "student_id": student_id,
                "section_id": section_id,
            }

        if not stats["exceeded"]:
            return {
                "checked": True,
                "exceeded": False,
                "sent": False,
                "reason": "within_limit",
                "stats": stats,
            }

        cache_key = (stats["student_id"], stats["section_id"])
        if not force and cls._notified_absences.get(cache_key) == stats["absent_count"]:
            return {
                "checked": True,
                "exceeded": True,
                "sent": False,
                "reason": "already_notified",
                "stats": stats,
            }

        subject, html_body, text_body = cls._build_absence_warning_content(stats)
        cc_list = [stats["lecturer_email"]] if (notify_lecturer and stats.get("lecturer_email")) else None
        delivery = cls.send_email(
            to_email=stats["student_email"],
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            cc=cc_list,
        )

        if delivery.get("sent") or delivery.get("mode") == "dry_run":
            cls._notified_absences[cache_key] = stats["absent_count"]

        return {
            "checked": True,
            "exceeded": True,
            "sent": delivery.get("sent", False),
            "mode": delivery.get("mode"),
            "delivery": delivery,
            "stats": stats,
        }

    @classmethod
    def check_and_warn_section(
        cls,
        section_id: str,
        threshold: Optional[float] = None,
        max_absences: Optional[int] = None,
        min_sessions: int = 1,
        force: bool = False,
        notify_lecturer: bool = False,
    ) -> Dict[str, Any]:
        """Quét toàn bộ sinh viên trong lớp học phần và gửi cảnh báo cho những bạn vắng quá quy định."""
        enr_tbl = Enrollment.__table__
        student_ids = [
            r[0]
            for r in db.session.execute(
                db.select(enr_tbl.c.studentid)
                .where(enr_tbl.c.sectionid == section_id)
                .order_by(enr_tbl.c.studentid.asc())
            ).all()
        ]

        summary: Dict[str, Any] = {
            "section_id": section_id,
            "checked": len(student_ids),
            "exceeded": 0,
            "sent": 0,
            "dry_run": 0,
            "skipped": 0,
            "failed": 0,
            "details": [],
        }

        for sid in student_ids:
            res = cls.send_absence_warning(
                student_id=sid,
                section_id=section_id,
                threshold=threshold,
                max_absences=max_absences,
                min_sessions=min_sessions,
                force=force,
                notify_lecturer=notify_lecturer,
            )
            summary["details"].append(res)
            if not res.get("exceeded"):
                continue
            summary["exceeded"] += 1
            if res.get("sent"):
                summary["sent"] += 1
            elif res.get("mode") == "dry_run":
                summary["dry_run"] += 1
            elif res.get("reason") == "already_notified":
                summary["skipped"] += 1
            else:
                summary["failed"] += 1

        return summary

    @classmethod
    def check_and_warn_session(
        cls,
        session_id: int,
        threshold: Optional[float] = None,
        max_absences: Optional[int] = None,
        min_sessions: int = 1,
        force: bool = False,
        notify_lecturer: bool = False,
    ) -> Dict[str, Any]:
        """Hàm tiện ích để gọi ngay sau khi giảng viên đóng 1 phiên điểm danh (chỉ cần truyền session_id)."""
        section_id = db.session.execute(
            db.select(AttendanceSession.section_id).where(AttendanceSession.session_id == session_id)
        ).scalar_one_or_none()
        if not section_id:
            return {
                "session_id": session_id,
                "checked": 0,
                "exceeded": 0,
                "sent": 0,
                "dry_run": 0,
                "skipped": 0,
                "failed": 0,
                "error": f"Attendance session #{session_id} not found.",
                "details": [],
            }
        result = cls.check_and_warn_section(
            section_id=section_id,
            threshold=threshold,
            max_absences=max_absences,
            min_sessions=min_sessions,
            force=force,
            notify_lecturer=notify_lecturer,
        )
        result["session_id"] = session_id
        return result

    @classmethod
    def check_and_warn_section_async(
        cls,
        section_id: str,
        **kwargs: Any,
    ) -> threading.Thread:
        """Chạy quét & gửi mail trên luồng nền để không làm chậm phản hồi HTTP."""
        app_obj = current_app._get_current_object()

        def _worker() -> None:
            with app_obj.app_context():
                try:
                    cls.check_and_warn_section(section_id=section_id, **kwargs)
                except Exception as err:
                    logger.exception("[EmailService ASYNC ERROR] section=%s: %s", section_id, err)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        return thread

    @classmethod
    def send_password_reset_email(
        cls,
        to_email: str,
        reset_link_or_code: str,
        username: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Gửi email đặt lại mật khẩu (hỗ trợ luôn tính năng Reset Password trong .env)."""
        display_name = username or to_email
        subject = "[Attendly] Password Reset Request"
        text_body = (
            f"Hello {display_name},\n\n"
            f"We received a request to reset your Attendly password.\n"
            f"Reset link / code: {reset_link_or_code}\n\n"
            f"If you did not request this, you can safely ignore this email.\n\n"
            f"Best regards,\nAttendly Support"
        )
        html_body = f"""<!DOCTYPE html>
<html>
<body style="font-family: Arial, sans-serif; color: #1f2937; padding: 20px;">
  <div style="max-width: 560px; margin: 0 auto; border: 1px solid #e5e7eb; border-radius: 10px; padding: 24px;">
    <h2 style="margin-top: 0; color: #1e293b;">Password Reset Request</h2>
    <p>Hello <strong>{html.escape(display_name)}</strong>,</p>
    <p>Use the link or verification code below to reset your Attendly password:</p>
    <p style="padding: 12px; background: #f3f4f6; border-radius: 6px; word-break: break-all;">
      <strong>{html.escape(str(reset_link_or_code))}</strong>
    </p>
    <p style="font-size: 12px; color: #6b7280;">If you did not request a password reset, please ignore this email.</p>
  </div>
</body>
</html>"""
        return cls.send_email(to_email=to_email, subject=subject, html_body=html_body, text_body=text_body)


email_service = EmailService()
