import csv
import io
from datetime import datetime
from typing import Any, Dict, List, Optional

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
from app.services.email_service import EmailService


class ReportService:
    """
    Service for compiling end-of-semester attendance analytics, student risk matrices,
    meeting-by-meeting breakdowns, and CSV export streams for lecturers and administrators.
    """

    @classmethod
    def get_section_report(
        cls,
        section_id: str,
        threshold: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Compile complete attendance statistics and per-student breakdown for a course section."""
        sec = CourseSection.query.filter_by(section_id=section_id).first()
        if not sec:
            return None

        c_tbl = Course.__table__
        c_row = db.session.execute(
            db.select(c_tbl.c.coursename, c_tbl.c.credits).where(c_tbl.c.courseid == sec.course_id)
        ).first()
        course_name = c_row.coursename if c_row else sec.course_id
        credits = c_row.credits if c_row else 3

        lecturer_name = "TBA"
        if sec.lecturer_id:
            l_tbl = Lecturer.__table__
            l_row = db.session.execute(
                db.select(l_tbl.c.fullname).where(l_tbl.c.lecturerid == sec.lecturer_id)
            ).first()
            if l_row and l_row.fullname:
                lecturer_name = l_row.fullname

        # Sessions ordered chronologically for meeting index (Meeting 1..N)
        sessions = (
            AttendanceSession.query.filter_by(section_id=section_id)
            .order_by(AttendanceSession.session_date.asc(), AttendanceSession.start_time.asc(), AttendanceSession.session_id.asc())
            .all()
        )
        total_sessions = len(sessions)
        session_ids = [s.session_id for s in sessions]

        # Enrolled students
        enr_tbl = Enrollment.__table__
        stu_tbl = Student.__table__
        usr_tbl = User.__table__

        stu_rows = db.session.execute(
            db.select(stu_tbl.c.studentid, stu_tbl.c.fullname, stu_tbl.c.phone, usr_tbl.c.email)
            .join(enr_tbl, enr_tbl.c.studentid == stu_tbl.c.studentid)
            .join(usr_tbl, usr_tbl.c.userid == stu_tbl.c.userid)
            .where(enr_tbl.c.sectionid == section_id)
            .order_by(stu_tbl.c.studentid.asc())
        ).all()

        records = (
            AttendanceRecord.query.filter(AttendanceRecord.session_id.in_(session_ids)).all()
            if session_ids
            else []
        )

        rec_by_stu_sess: Dict[tuple, AttendanceRecord] = {}
        rec_by_sess: Dict[int, List[AttendanceRecord]] = {}
        for r in records:
            rec_by_stu_sess[(str(r.student_id), r.session_id)] = r
            rec_by_sess.setdefault(r.session_id, []).append(r)

        eff_threshold = EmailService.default_threshold() if threshold is None else float(threshold)
        min_attendance_pct = round((1.0 - eff_threshold) * 100, 1)

        student_reports: List[Dict[str, Any]] = []
        totals = {"PRESENT": 0, "LATE": 0, "EXCUSED": 0, "ABSENT": 0}
        at_risk_count = 0

        for stu in stu_rows:
            sid = str(stu.studentid)
            counts = {"PRESENT": 0, "LATE": 0, "EXCUSED": 0, "ABSENT": 0}
            session_statuses: List[Dict[str, Any]] = []

            for idx, sess in enumerate(sessions, start=1):
                rec = rec_by_stu_sess.get((sid, sess.session_id))
                st = (rec.status if rec and rec.status else "ABSENT").upper()
                counts[st] = counts.get(st, 0) + 1
                totals[st] = totals.get(st, 0) + 1
                session_statuses.append({
                    "meeting_number": idx,
                    "session_id": sess.session_id,
                    "session_date": sess.session_date.isoformat() if sess.session_date else None,
                    "status": st,
                    "check_time": rec.check_time.isoformat() if (rec and rec.check_time) else None,
                })

            attended = counts["PRESENT"] + counts["LATE"]
            attendance_rate = round((attended / total_sessions) * 100, 1) if total_sessions > 0 else 100.0
            absence_rate = round((counts["ABSENT"] / total_sessions), 4) if total_sessions > 0 else 0.0
            is_exceeded = total_sessions > 0 and counts["ABSENT"] > 0 and absence_rate >= eff_threshold

            if is_exceeded:
                at_risk_count += 1

            student_reports.append({
                "student_id": sid,
                "fullname": stu.fullname or sid,
                "email": stu.email,
                "phone": stu.phone,
                "total_sessions": total_sessions,
                "present": counts["PRESENT"],
                "late": counts["LATE"],
                "excused": counts["EXCUSED"],
                "absent": counts["ABSENT"],
                "attended": attended,
                "attendance_rate": attendance_rate,
                "absence_rate": round(absence_rate * 100, 1),
                "is_at_risk": is_exceeded,
                "standing": "Warning" if is_exceeded else "On track",
                "sessions": session_statuses,
            })

        meeting_summaries: List[Dict[str, Any]] = []
        for idx, sess in enumerate(sessions, start=1):
            s_recs = rec_by_sess.get(sess.session_id, [])
            m_counts = {"PRESENT": 0, "LATE": 0, "EXCUSED": 0, "ABSENT": 0}
            for r in s_recs:
                st = (r.status or "ABSENT").upper()
                m_counts[st] = m_counts.get(st, 0) + 1
            m_total = len(stu_rows) or len(s_recs)
            m_attended = m_counts["PRESENT"] + m_counts["LATE"]
            m_rate = round((m_attended / m_total) * 100, 1) if m_total > 0 else 0.0
            meeting_summaries.append({
                "meeting_number": idx,
                "session_id": sess.session_id,
                "session_date": sess.session_date.isoformat() if sess.session_date else None,
                "start_time": sess.start_time.isoformat() if sess.start_time else None,
                "end_time": sess.end_time.isoformat() if sess.end_time else None,
                "pin_code": sess.pin_code,
                "present": m_counts["PRESENT"],
                "late": m_counts["LATE"],
                "excused": m_counts["EXCUSED"],
                "absent": m_counts["ABSENT"],
                "total": m_total,
                "attendance_rate": m_rate,
            })

        total_possible = sum(totals.values())
        overall_attended = totals["PRESENT"] + totals["LATE"]
        overall_rate = round((overall_attended / total_possible) * 100, 1) if total_possible > 0 else 100.0

        return {
            "section": {
                "section_id": sec.section_id,
                "course_id": sec.course_id,
                "course_name": course_name,
                "credits": credits,
                "section_name": sec.section_name,
                "semester": sec.semester,
                "room": sec.room,
                "lecturer_id": sec.lecturer_id,
                "lecturer_name": lecturer_name,
            },
            "overview": {
                "enrolled_students": len(stu_rows),
                "total_sessions": total_sessions,
                "average_attendance": overall_rate,
                "present_total": totals["PRESENT"],
                "late_total": totals["LATE"],
                "excused_total": totals["EXCUSED"],
                "absent_total": totals["ABSENT"],
                "at_risk_students": at_risk_count,
                "on_track_students": max(0, len(stu_rows) - at_risk_count),
                "warning_threshold_percent": round(eff_threshold * 100, 1),
                "min_attendance_percent": min_attendance_pct,
            },
            "students": student_reports,
            "meetings": list(reversed(meeting_summaries)),
        }

    @classmethod
    def export_section_csv(cls, section_id: str, include_sessions: bool = True) -> Optional[str]:
        """Generate a UTF-8 CSV report string for end-of-semester attendance export."""
        report = cls.get_section_report(section_id)
        if not report:
            return None

        sec = report["section"]
        ov = report["overview"]
        students = report["students"]

        buf = io.StringIO()
        # Write UTF-8 BOM so Microsoft Excel opens Vietnamese/Unicode names cleanly
        buf.write("\ufeff")
        writer = csv.writer(buf)

        writer.writerow(["END-OF-SEMESTER ATTENDANCE REPORT - ATTENDLY"])
        writer.writerow(["Course", f"{sec['course_id']} - {sec['course_name']}"])
        writer.writerow(["Section", f"{sec['section_id']} ({sec['section_name']})"])
        writer.writerow(["Semester", sec["semester"]])
        writer.writerow(["Room", sec["room"] or "TBA"])
        writer.writerow(["Lecturer", sec["lecturer_name"]])
        writer.writerow(["Total Meetings", ov["total_sessions"]])
        writer.writerow(["Enrolled Students", ov["enrolled_students"]])
        writer.writerow(["Average Attendance Rate", f"{ov['average_attendance']}%"])
        writer.writerow(["Generated At", datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
        writer.writerow([])

        # Build table headers
        headers = [
            "Student ID",
            "Full Name",
            "Email",
            "Total Meetings",
            "Present",
            "Late",
            "Excused",
            "Absent (Unexcused)",
            "Attendance Rate (%)",
            "Absence Rate (%)",
            "Academic Standing",
        ]

        if include_sessions and students and students[0].get("sessions"):
            for s_meta in students[0]["sessions"]:
                date_label = s_meta["session_date"] or ""
                headers.append(f"M{s_meta['meeting_number']} ({date_label})")

        writer.writerow(headers)

        for stu in students:
            row = [
                stu["student_id"],
                stu["fullname"],
                stu["email"] or "",
                stu["total_sessions"],
                stu["present"],
                stu["late"],
                stu["excused"],
                stu["absent"],
                f"{stu['attendance_rate']}%",
                f"{stu['absence_rate']}%",
                stu["standing"],
            ]
            if include_sessions and stu.get("sessions"):
                for s_meta in stu["sessions"]:
                    row.append(s_meta["status"])
            writer.writerow(row)

        return buf.getvalue()


report_service = ReportService()
