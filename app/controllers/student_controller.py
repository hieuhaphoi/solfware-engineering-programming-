from datetime import datetime

from flask import Blueprint, flash, jsonify, redirect, render_template, request, session, url_for

from app.extensions.database import db
from app.models import AttendanceRecord, AttendanceSession, Course, CourseSection, Enrollment, Lecturer, Student
from app.services.auth_factory import role_required

student_bp = Blueprint("student", __name__, url_prefix="/student")


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


def _current_student_id():
    # Dùng Core query lấy student_id để tránh Identity Map Collision
    uid = session.get("user_id")
    if uid is not None:
        sid = db.session.execute(
            db.select(Student.__table__.c.studentid).where(Student.__table__.c.userid == uid)
        ).scalar_one_or_none()
        if sid:
            return sid
    return session.get("profile_id")


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


def _get_lecturer_map(lecturer_ids):
    clean_ids = [lid for lid in lecturer_ids if lid]
    if not clean_ids:
        return {}
    l_tbl = Lecturer.__table__
    rows = db.session.execute(
        db.select(l_tbl.c.lecturerid, l_tbl.c.fullname).where(l_tbl.c.lecturerid.in_(clean_ids))
    ).all()
    return {r.lecturerid: r.fullname for r in rows}


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


def _load_enrolled_sections(student_id):
    enr_tbl = Enrollment.__table__
    sec_ids = [
        r[0]
        for r in db.session.execute(
            db.select(enr_tbl.c.sectionid).where(enr_tbl.c.studentid == student_id)
        ).all()
    ]
    if not sec_ids:
        return [], {}, {}

    sections = (
        CourseSection.query.filter(CourseSection.section_id.in_(sec_ids))
        .order_by(CourseSection.semester.desc(), CourseSection.section_id.asc())
        .all()
    )
    course_map = _get_course_map([s.course_id for s in sections])
    lecturer_map = _get_lecturer_map([s.lecturer_id for s in sections])
    return sections, course_map, lecturer_map


# --- Trang danh sách môn học & check-in bằng mã PIN ---

@student_bp.route("", methods=["GET"])
@student_bp.route("/", methods=["GET"])
@student_bp.route("/courses", methods=["GET"])
@student_bp.route("/checkin", methods=["GET"])
@role_required("student")
def courses_checkin():
    student_id = _current_student_id()
    if not student_id:
        if _wants_json():
            return jsonify({"success": False, "message": "Student profile not found."}), 404
        flash("Student profile not found.", "danger")
        return redirect(url_for("auth.login"))

    sections, course_map, lecturer_map = _load_enrolled_sections(student_id)
    sec_ids = [s.section_id for s in sections]
    sec_lookup = {s.section_id: s for s in sections}

    all_sessions = (
        AttendanceSession.query.filter(AttendanceSession.section_id.in_(sec_ids))
        .order_by(AttendanceSession.start_time.desc())
        .all()
        if sec_ids
        else []
    )
    session_ids = [s.session_id for s in all_sessions]

    my_records = (
        AttendanceRecord.query.filter(
            AttendanceRecord.student_id == student_id,
            AttendanceRecord.session_id.in_(session_ids),
        ).all()
        if session_ids
        else []
    )
    record_by_session = {r.session_id: r for r in my_records}

    now = datetime.now()
    active_sessions = []
    open_sec_ids = set()

    for sess in all_sessions:
        rem = _remaining_seconds(sess, now)
        if rem <= 0:
            continue
        sec = sec_lookup.get(sess.section_id)
        if not sec:
            continue
        open_sec_ids.add(sec.section_id)
        rec = record_by_session.get(sess.session_id)
        my_status = (rec.status or "ABSENT").upper() if rec else "ABSENT"
        c_info = course_map.get(sec.course_id, {})

        active_sessions.append({
            "session_id": sess.session_id,
            "section_id": sec.section_id,
            "course_id": sec.course_id,
            "course_name": c_info.get("course_name", sec.course_id),
            "section_name": sec.section_name,
            "semester": sec.semester,
            "room": sec.room,
            "lecturer_name": lecturer_map.get(sec.lecturer_id, "TBA"),
            "session_date": sess.session_date.isoformat() if sess.session_date else None,
            "start_time": sess.start_time.isoformat() if sess.start_time else None,
            "end_time": sess.end_time.isoformat() if sess.end_time else None,
            "remaining_seconds": rem,
            "my_status": my_status,
            "already_checked_in": my_status in {"PRESENT", "LATE"},
            "check_time": rec.check_time.isoformat() if rec and rec.check_time else None,
        })

    # Ưu tiên hiển thị phiên đang mở mà sinh viên chưa điểm danh lên thẻ trên cùng
    active_session = next((s for s in active_sessions if not s["already_checked_in"]), None)
    if not active_session and active_sessions:
        active_session = active_sessions[0]

    sessions_by_sec = {}
    for sess in all_sessions:
        sessions_by_sec.setdefault(sess.section_id, []).append(sess)

    courses_list = []
    for sec in sections:
        c_info = course_map.get(sec.course_id, {})
        sec_sessions = sessions_by_sec.get(sec.section_id, [])
        sec_recs = [record_by_session[s.session_id] for s in sec_sessions if s.session_id in record_by_session]
        sec_sum = _summarize_records(sec_recs)

        courses_list.append({
            "section_id": sec.section_id,
            "course_id": sec.course_id,
            "course_name": c_info.get("course_name", sec.course_id),
            "credits": c_info.get("credits"),
            "section_name": sec.section_name,
            "semester": sec.semester,
            "room": sec.room,
            "lecturer_id": sec.lecturer_id,
            "lecturer_name": lecturer_map.get(sec.lecturer_id, "TBA"),
            "has_open_session": sec.section_id in open_sec_ids,
            "total_sessions": len(sec_sessions),
            "attendance_rate": sec_sum["attendance_rate"],
            "summary": sec_sum,
        })

    overall = _summarize_records(my_records)
    semesters = sorted({s.semester for s in sections if s.semester}, reverse=True)
    stats = {
        "total_courses": len(courses_list),
        "open_sessions": len(active_sessions),
        "current_semester": semesters[0] if semesters else "",
        **overall,
    }

    if _wants_json():
        return jsonify({
            "success": True,
            "student_id": student_id,
            "stats": stats,
            "active_session": active_session,
            "active_sessions": active_sessions,
            "courses": courses_list,
        }), 200

    return render_template(
        "student/courses_checkin.html",
        courses=courses_list,
        sections=courses_list,
        active_session=active_session,
        active_sessions=active_sessions,
        stats=stats,
    )


# --- Lịch sử điểm danh của sinh viên ---

@student_bp.route("/history", methods=["GET"])
@student_bp.route("/attendance-history", methods=["GET"])
@role_required("student")
def attendance_history():
    student_id = _current_student_id()
    if not student_id:
        if _wants_json():
            return jsonify({"success": False, "message": "Student profile not found."}), 404
        flash("Student profile not found.", "danger")
        return redirect(url_for("auth.login"))

    section_filter = (request.args.get("section_id") or "").strip()
    status_filter = (request.args.get("status") or "").strip().upper()

    sections, course_map, lecturer_map = _load_enrolled_sections(student_id)
    sec_lookup = {s.section_id: s for s in sections}
    sec_options = [
        {
            "section_id": s.section_id,
            "course_id": s.course_id,
            "course_name": course_map.get(s.course_id, {}).get("course_name", s.course_id),
            "section_name": s.section_name,
            "semester": s.semester,
            "room": s.room,
            "lecturer_name": lecturer_map.get(s.lecturer_id, "TBA"),
        }
        for s in sections
    ]

    rows = (
        db.session.query(AttendanceRecord, AttendanceSession)
        .join(AttendanceSession, AttendanceRecord.session_id == AttendanceSession.session_id)
        .filter(AttendanceRecord.student_id == student_id)
        .order_by(AttendanceSession.session_date.desc(), AttendanceSession.start_time.desc())
        .all()
    )

    now = datetime.now()
    open_sessions_by_sec = {}
    all_items = []
    by_section = {}
    for rec, sess in rows:
        sec = sec_lookup.get(sess.section_id)
        if not sec:
            sec = CourseSection.query.filter_by(section_id=sess.section_id).first()
        cid = sec.course_id if sec else ""
        c_name = course_map.get(cid, {}).get("course_name", cid)
        status_val = (rec.status or "ABSENT").upper()
        rem = _remaining_seconds(sess, now)

        item = {
            "record_id": rec.record_id,
            "session_id": sess.session_id,
            "section_id": sess.section_id,
            "course_id": cid,
            "course_name": c_name,
            "section_name": sec.section_name if sec else sess.section_id,
            "semester": sec.semester if sec else "",
            "room": sec.room if sec else None,
            "lecturer_name": lecturer_map.get(sec.lecturer_id, "TBA") if sec else "TBA",
            "session_date": sess.session_date.isoformat() if sess.session_date else None,
            "start_time": sess.start_time.isoformat() if sess.start_time else None,
            "end_time": sess.end_time.isoformat() if sess.end_time else None,
            "check_time": rec.check_time.isoformat() if rec.check_time else None,
            "status": status_val,
            "absent": bool(rec.absent),
            "is_open": rem > 0,
            "remaining_seconds": rem,
        }
        all_items.append(item)
        by_section.setdefault(sess.section_id, []).append(item)
        if rem > 0 and sess.section_id not in open_sessions_by_sec:
            open_sessions_by_sec[sess.section_id] = item

    filtered = []
    for item in all_items:
        if section_filter and item["section_id"] != section_filter:
            continue
        if status_filter and item["status"] != status_filter:
            continue
        filtered.append(item)

    summary = _summarize_records(filtered)
    section_summaries = [
        {
            **opt,
            **_summarize_records(by_section.get(opt["section_id"], [])),
        }
        for opt in sec_options
    ]

    current_section = None
    if section_filter:
        current_section = next((s for s in section_summaries if s["section_id"] == section_filter), None)
    if not current_section and section_summaries:
        current_section = section_summaries[0]

    open_session = None
    if section_filter and section_filter in open_sessions_by_sec:
        open_session = open_sessions_by_sec[section_filter]
    elif open_sessions_by_sec:
        open_session = next(iter(open_sessions_by_sec.values()))

    if _wants_json():
        return jsonify({
            "success": True,
            "student_id": student_id,
            "summary": summary,
            "current_section": current_section,
            "open_session": open_session,
            "section_summaries": section_summaries,
            "records": filtered,
            "sections": sec_options,
        }), 200

    return render_template(
        "student/attendance_history.html",
        records=filtered,
        sections=sec_options,
        current_section=current_section,
        open_session=open_session,
        summary=summary,
        section_summaries=section_summaries,
        filters={"section_id": section_filter, "status": status_filter},
    )
