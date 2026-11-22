from datetime import datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func

from app.extensions.database import db
from app.models import AttendanceRecord, AttendanceSession, Course, CourseSection, Enrollment, Lecturer, Student, User
from app.services.auth_factory import role_required

lecturer_bp = Blueprint("lecturer", __name__, url_prefix="/lecturer")


def _wants_json():
    return request.is_json or request.accept_mimetypes.best == "application/json"


def _to_naive(dt):
    return dt.replace(tzinfo=None) if dt and dt.tzinfo else dt


def _remaining_seconds(att_session, now=None):
    if not att_session or not att_session.end_time:
        return 0
    now = now or datetime.now()
    diff = int((_to_naive(att_session.end_time) - now).total_seconds())
    return max(0, diff)


def _current_lecturer_id():
    # Dùng Core query lấy lecturer_id để tránh đụng Identity Map của SQLAlchemy
    uid = session.get("user_id")
    if uid is not None:
        lid = db.session.execute(
            db.select(Lecturer.__table__.c.lecturerid).where(Lecturer.__table__.c.userid == uid)
        ).scalar_one_or_none()
        if lid:
            return lid
    return session.get("profile_id")


def _get_student_map(student_ids):
    if not student_ids:
        return {}
    stu_tbl = Student.__table__
    usr_tbl = User.__table__
    rows = db.session.execute(
        db.select(stu_tbl.c.studentid, stu_tbl.c.fullname, stu_tbl.c.phone, usr_tbl.c.email)
        .join(usr_tbl, stu_tbl.c.userid == usr_tbl.c.userid)
        .where(stu_tbl.c.studentid.in_(student_ids))
    ).all()
    return {
        r.studentid: {"student_id": r.studentid, "fullname": r.fullname, "phone": r.phone, "email": r.email}
        for r in rows
    }


def _get_course_map(course_ids):
    if not course_ids:
        return {}
    c_tbl = Course.__table__
    rows = db.session.execute(
        db.select(c_tbl.c.courseid, c_tbl.c.coursename, c_tbl.c.credits).where(c_tbl.c.courseid.in_(course_ids))
    ).all()
    return {
        r.courseid: {"course_id": r.courseid, "course_name": r.coursename, "credits": r.credits}
        for r in rows
    }


def _serialize_session(att_session, now=None):
    rem = _remaining_seconds(att_session, now)
    return {
        "session_id": att_session.session_id,
        "section_id": att_session.section_id,
        "session_date": att_session.session_date.isoformat() if att_session.session_date else None,
        "pin_code": att_session.pin_code,
        "start_time": att_session.start_time.isoformat() if att_session.start_time else None,
        "end_time": att_session.end_time.isoformat() if att_session.end_time else None,
        "is_open": rem > 0,
        "remaining_seconds": rem,
    }


def _summarize_records(records):
    counts = {"PRESENT": 0, "LATE": 0, "EXCUSED": 0, "ABSENT": 0}
    for r in records:
        st = (r["status"] if isinstance(r, dict) else (r.status or "ABSENT")).upper()
        counts[st] = counts.get(st, 0) + 1
    total = len(records)
    attended = counts["PRESENT"] + counts["LATE"]
    rate = round((attended / total) * 100, 1) if total else 0.0
    return {
        "total": total,
        "present": counts["PRESENT"],
        "late": counts["LATE"],
        "excused": counts["EXCUSED"],
        "absent": counts["ABSENT"],
        "attendance_rate": rate,
    }


def _build_session_records(session_id):
    recs = (
        AttendanceRecord.query.filter_by(session_id=session_id)
        .order_by(AttendanceRecord.student_id.asc())
        .all()
    )
    stu_map = _get_student_map([r.student_id for r in recs])
    result = []
    for r in recs:
        info = stu_map.get(r.student_id, {})
        result.append({
            "record_id": r.record_id,
            "session_id": r.session_id,
            "student_id": r.student_id,
            "fullname": info.get("fullname") or r.student_id,
            "email": info.get("email"),
            "phone": info.get("phone"),
            "status": (r.status or "ABSENT").upper(),
            "absent": bool(r.absent),
            "check_time": r.check_time.isoformat() if r.check_time else None,
        })
    return result


# --- Danh sách lớp được phân công ---

@lecturer_bp.route("", methods=["GET"])
@lecturer_bp.route("/", methods=["GET"])
@lecturer_bp.route("/sections", methods=["GET"])
@role_required("lecturer")
def assigned_sections():
    lecturer_id = _current_lecturer_id()
    if not lecturer_id:
        if _wants_json():
            return jsonify({"success": False, "message": "Lecturer profile not found."}), 404
        flash("Lecturer profile not found.", "danger")
        return redirect(url_for("auth.login"))

    semester = (request.args.get("semester") or "").strip()
    q = (request.args.get("q") or "").strip().lower()

    query = CourseSection.query.filter_by(lecturer_id=lecturer_id)
    if semester:
        query = query.filter(CourseSection.semester == semester)
    sections = query.order_by(CourseSection.semester.desc(), CourseSection.section_id.asc()).all()

    course_map = _get_course_map([s.course_id for s in sections])
    sec_ids = [s.section_id for s in sections]

    # Đếm sĩ số và lấy toàn bộ phiên điểm danh của các lớp phụ trách
    enroll_counts = {}
    if sec_ids:
        enr_tbl = Enrollment.__table__
        rows = db.session.execute(
            db.select(enr_tbl.c.sectionid, func.count(enr_tbl.c.studentid))
            .where(enr_tbl.c.sectionid.in_(sec_ids))
            .group_by(enr_tbl.c.sectionid)
        ).all()
        enroll_counts = {r[0]: r[1] for r in rows}

    all_sessions = (
        AttendanceSession.query.filter(AttendanceSession.section_id.in_(sec_ids))
        .order_by(AttendanceSession.start_time.desc())
        .all()
        if sec_ids
        else []
    )
    sessions_by_sec = {}
    for sess in all_sessions:
        sessions_by_sec.setdefault(sess.section_id, []).append(sess)

    all_session_ids = [s.session_id for s in all_sessions]
    all_records = (
        AttendanceRecord.query.filter(AttendanceRecord.session_id.in_(all_session_ids)).all()
        if all_session_ids
        else []
    )
    records_by_session = {}
    for rec in all_records:
        records_by_session.setdefault(rec.session_id, []).append(rec)

    now = datetime.now()
    section_items = []
    active_banner = None
    semesters = sorted({s.semester for s in sections if s.semester}, reverse=True)

    for sec in sections:
        c_info = course_map.get(sec.course_id, {})
        c_name = c_info.get("course_name") or sec.course_id
        if q and q not in f"{sec.section_id} {sec.course_id} {c_name} {sec.section_name} {sec.room or ''}".lower():
            continue

        sec_sessions = sessions_by_sec.get(sec.section_id, [])
        sec_records = [r for sess in sec_sessions for r in records_by_session.get(sess.session_id, [])]
        sec_summary = _summarize_records(sec_records)

        open_sess = next((sess for sess in sec_sessions if _remaining_seconds(sess, now) > 0), None)
        open_data = _serialize_session(open_sess, now) if open_sess else None
        if open_data and open_sess:
            open_recs = records_by_session.get(open_sess.session_id, [])
            open_sum = _summarize_records(open_recs)
            open_data["checked_in_count"] = open_sum["present"] + open_sum["late"]
            open_data["total_students"] = enroll_counts.get(sec.section_id, len(open_recs))
            open_data["meeting_number"] = len(sec_sessions)

        item = {
            "section_id": sec.section_id,
            "course_id": sec.course_id,
            "course_name": c_name,
            "credits": c_info.get("credits"),
            "section_name": sec.section_name,
            "semester": sec.semester,
            "room": sec.room,
            "enrolled_count": enroll_counts.get(sec.section_id, 0),
            "total_sessions": len(sec_sessions),
            "attendance_rate": sec_summary["attendance_rate"],
            "active_session": open_data,
        }
        section_items.append(item)
        if open_data and not active_banner:
            active_banner = {**item, "session": open_data}

    overall = _summarize_records(all_records)
    total_enrolled = 0
    if sec_ids:
        enr_tbl = Enrollment.__table__
        total_enrolled = (
            db.session.execute(
                db.select(func.count(func.distinct(enr_tbl.c.studentid))).where(enr_tbl.c.sectionid.in_(sec_ids))
            ).scalar()
            or 0
        )

    stats = {
        "assigned_sections": len(section_items),
        "enrolled_students": total_enrolled,
        "total_sessions": len(all_sessions),
        "average_attendance": overall["attendance_rate"],
        "current_semester": semester or (semesters[0] if semesters else ""),
    }

    if _wants_json():
        return jsonify({
            "success": True,
            "lecturer_id": lecturer_id,
            "stats": stats,
            "active_session": active_banner,
            "sections": section_items,
        }), 200

    return render_template(
        "lecturer/assigned_sections.html",
        sections=section_items,
        stats=stats,
        active_session=active_banner,
        semesters=semesters,
        filters={"semester": semester, "q": q},
    )


# --- Màn hình điểm danh lớp học phần ---

@lecturer_bp.route("/attendance", methods=["GET"])
@lecturer_bp.route("/sections/<string:section_id>/attendance", methods=["GET"])
@role_required("lecturer")
def class_attendance(section_id=None):
    lecturer_id = _current_lecturer_id()
    if not lecturer_id:
        if _wants_json():
            return jsonify({"success": False, "message": "Lecturer profile not found."}), 404
        flash("Lecturer profile not found.", "danger")
        return redirect(url_for("auth.login"))

    sections = (
        CourseSection.query.filter_by(lecturer_id=lecturer_id)
        .order_by(CourseSection.semester.desc(), CourseSection.section_id.asc())
        .all()
    )
    course_map = _get_course_map([s.course_id for s in sections])
    sec_list = [
        {
            **s.to_dict(),
            "course_name": course_map.get(s.course_id, {}).get("course_name", s.course_id),
        }
        for s in sections
    ]

    if not sections:
        empty_summary = _summarize_records([])
        if _wants_json():
            return jsonify({
                "success": True,
                "sections": [],
                "current_section": None,
                "sessions": [],
                "selected_session": None,
                "records": [],
                "enrolled_students": [],
                "summary": empty_summary,
                "remaining_seconds": 0,
            }), 200
        return render_template(
            "lecturer/class_attendance.html",
            sections=[],
            current_section=None,
            sessions=[],
            active_session=None,
            selected_session=None,
            records=[],
            enrolled_students=[],
            summary=empty_summary,
            remaining_seconds=0,
        )

    target_sec_id = (section_id or request.args.get("section_id") or "").strip()
    now = datetime.now()

    if target_sec_id:
        current_sec = next((s for s in sections if s.section_id == target_sec_id), None)
        if not current_sec:
            if _wants_json():
                return jsonify({"success": False, "message": "Section not found or not assigned to you."}), 403
            flash("You do not have access to this course section.", "danger")
            return redirect(url_for("lecturer.assigned_sections"))
    else:
        # Nếu vào thẳng /lecturer/attendance thì ưu tiên lớp đang mở phiên PIN, không có thì lấy lớp đầu tiên
        sec_ids = [s.section_id for s in sections]
        open_any = (
            AttendanceSession.query.filter(
                AttendanceSession.section_id.in_(sec_ids),
                AttendanceSession.end_time > now,
            )
            .order_by(AttendanceSession.start_time.desc())
            .first()
        )
        current_sec = next((s for s in sections if open_any and s.section_id == open_any.section_id), sections[0])

    current_sec_data = {
        **current_sec.to_dict(),
        "course_name": course_map.get(current_sec.course_id, {}).get("course_name", current_sec.course_id),
    }

    sec_sessions = (
        AttendanceSession.query.filter_by(section_id=current_sec.section_id)
        .order_by(AttendanceSession.start_time.desc(), AttendanceSession.session_id.desc())
        .all()
    )
    sessions_data = [_serialize_session(s, now) for s in sec_sessions]
    active_sess = next((s for s in sec_sessions if _remaining_seconds(s, now) > 0), None)

    req_session_id = request.args.get("session_id", type=int)
    selected_sess = None
    if req_session_id:
        selected_sess = next((s for s in sec_sessions if s.session_id == req_session_id), None)
    if not selected_sess:
        selected_sess = active_sess or (sec_sessions[0] if sec_sessions else None)

    # Danh sách sinh viên đã đăng ký lớp này
    enr_tbl = Enrollment.__table__
    enrolled_ids = [
        r[0]
        for r in db.session.execute(
            db.select(enr_tbl.c.studentid)
            .where(enr_tbl.c.sectionid == current_sec.section_id)
            .order_by(enr_tbl.c.studentid.asc())
        ).all()
    ]
    stu_map = _get_student_map(enrolled_ids)
    enrolled_students = [stu_map[sid] for sid in enrolled_ids if sid in stu_map]

    records = _build_session_records(selected_sess.session_id) if selected_sess else []
    summary = _summarize_records(records)
    selected_data = _serialize_session(selected_sess, now) if selected_sess else None
    active_data = _serialize_session(active_sess, now) if active_sess else None
    remaining_seconds = active_data["remaining_seconds"] if active_data else 0

    if _wants_json():
        return jsonify({
            "success": True,
            "sections": sec_list,
            "current_section": current_sec_data,
            "sessions": sessions_data,
            "active_session": active_data,
            "selected_session": selected_data,
            "records": records,
            "enrolled_students": enrolled_students,
            "summary": summary,
            "remaining_seconds": remaining_seconds,
        }), 200

    return render_template(
        "lecturer/class_attendance.html",
        sections=sec_list,
        current_section=current_sec_data,
        sessions=sessions_data,
        active_session=active_data,
        selected_session=selected_data,
        records=records,
        enrolled_students=enrolled_students,
        summary=summary,
        remaining_seconds=remaining_seconds,
    )


# --- API hỗ trợ pin_timer.js load danh sách phiên & cập nhật điểm danh realtime ---

@lecturer_bp.route("/sections/<string:section_id>/sessions", methods=["GET"])
@role_required("lecturer")
def list_section_sessions(section_id):
    lecturer_id = _current_lecturer_id()
    sec = CourseSection.query.filter_by(section_id=section_id, lecturer_id=lecturer_id).first()
    if not sec:
        return jsonify({"success": False, "message": "Section not found or not assigned to you."}), 403

    now = datetime.now()
    sec_sessions = (
        AttendanceSession.query.filter_by(section_id=section_id)
        .order_by(AttendanceSession.start_time.desc(), AttendanceSession.session_id.desc())
        .all()
    )
    return jsonify({
        "success": True,
        "section_id": section_id,
        "sessions": [_serialize_session(s, now) for s in sec_sessions],
    }), 200


@lecturer_bp.route("/sessions/<int:session_id>/records", methods=["GET"])
@role_required("lecturer")
def session_records_api(session_id):
    lecturer_id = _current_lecturer_id()
    att_session = AttendanceSession.query.filter_by(session_id=session_id).first()
    if not att_session:
        return jsonify({"success": False, "message": "Attendance session not found."}), 404

    sec = CourseSection.query.filter_by(section_id=att_session.section_id, lecturer_id=lecturer_id).first()
    if not sec:
        return jsonify({"success": False, "message": "You do not have access to this session."}), 403

    now = datetime.now()
    records = _build_session_records(session_id)
    sess_data = _serialize_session(att_session, now)
    return jsonify({
        "success": True,
        "session": sess_data,
        "remaining_seconds": sess_data["remaining_seconds"],
        "summary": _summarize_records(records),
        "records": records,
    }), 200
