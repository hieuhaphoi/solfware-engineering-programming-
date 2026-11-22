import re
from datetime import date

from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from app.extensions.database import db
from app.models import Admin, Course, CourseSection, Enrollment, Lecturer, Student, User
from app.services.auth_factory import role_required
from app.services.csv_service import CSVService

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

# Chuẩn hóa trạng thái khớp đúng CHECK (status IN ('Active', 'Locked', 'Pending')) của PostgreSQL
STATUS_MAP = {
    "active": "Active",
    "locked": "Locked",
    "inactive": "Locked",
    "pending": "Pending",
}


def _wants_json():
    return request.is_json or request.accept_mimetypes.best == "application/json"


def _get_data():
    return request.get_json(silent=True) or {} if request.is_json else request.form.to_dict()


def _reply(ok, msg, code=200, next_url=None, cat="info", extra=None):
    if _wants_json():
        return jsonify({"success": ok, "message": msg, **(extra or {})}), code
    if msg:
        flash(msg, cat)
    return redirect(next_url or url_for("admin.dashboard"))


def _normalize_status(raw, default="Active"):
    if raw is None or str(raw).strip() == "":
        return default
    return STATUS_MAP.get(str(raw).strip().lower())


def _normalize_phone(raw_phone):
    cleaned = re.sub(r"[\s\-\.]", "", (raw_phone or "").strip())
    if not cleaned:
        return None, None
    if cleaned.startswith("+84"):
        cleaned = "0" + cleaned[3:]
    if len(cleaned) > 11:
        return None, "Phone number must not exceed 11 characters."
    return cleaned, None


def _format_user(obj, role_name):
    data = obj.to_dict() if hasattr(obj, "to_dict") else {}
    profile_id = getattr(obj, "admin_id", None) or getattr(obj, "lecturer_id", None) or getattr(obj, "student_id", None)
    return {
        **data,
        "user_id": obj.user_id,
        "username": obj.username,
        "email": obj.email,
        "status": obj.status,
        "role": role_name,
        "profile_id": profile_id,
        "fullname": getattr(obj, "fullname", None) or getattr(obj, "role", None) or obj.username,
        "phone": getattr(obj, "phone", None),
    }


def _evict_mismatched_user(user_id, target_model):
    # Gỡ bản ghi User cùng user_id khác class khỏi Identity Map trước khi load lớp con
    for obj in list(db.session.identity_map.values()):
        if isinstance(obj, User) and getattr(obj, "user_id", None) == user_id and type(obj) is not target_model:
            db.session.expunge(obj)


def _find_user(user_id):
    # Xác định bảng con bằng Core query trước để tránh SQLAlchemy Polymorphic Identity Map Collision
    checks = (
        ("admin", Admin, Admin.__table__.c.adminid, Admin.__table__.c.userid),
        ("lecturer", Lecturer, Lecturer.__table__.c.lecturerid, Lecturer.__table__.c.userid),
        ("student", Student, Student.__table__.c.studentid, Student.__table__.c.userid),
    )
    for role_name, model, pk_col, uid_col in checks:
        pk_val = db.session.execute(db.select(pk_col).where(uid_col == user_id)).scalar_one_or_none()
        if pk_val is not None:
            _evict_mismatched_user(user_id, model)
            return db.session.query(model).filter(pk_col == pk_val).first(), role_name

    uid_exists = db.session.execute(
        db.select(User.__table__.c.userid).where(User.__table__.c.userid == user_id)
    ).scalar_one_or_none()
    if uid_exists is not None:
        _evict_mismatched_user(user_id, User)
        return db.session.query(User).filter(User.user_id == user_id).first(), "user"
    return None, None


def _auto_id(role_name, model, col_name):
    prefix = {"admin": "AD", "lecturer": "GV", "student": "SV"}[role_name]
    col = model.__table__.c[col_name]
    num = db.session.execute(db.select(func.count()).select_from(model.__table__)).scalar() + 1
    while True:
        code = f"{prefix}{num:04d}"
        if db.session.execute(db.select(col).where(col == code)).scalar_one_or_none() is None:
            return code
        num += 1


# --- Tổng quan học kỳ ---

@admin_bp.route("", methods=["GET"])
@admin_bp.route("/", methods=["GET"])
@role_required("admin")
def dashboard():
    stats = {
        "total_users": db.session.execute(db.select(func.count()).select_from(User.__table__)).scalar(),
        "total_admins": db.session.execute(db.select(func.count()).select_from(Admin.__table__)).scalar(),
        "total_lecturers": db.session.execute(db.select(func.count()).select_from(Lecturer.__table__)).scalar(),
        "total_students": db.session.execute(db.select(func.count()).select_from(Student.__table__)).scalar(),
        "total_courses": db.session.execute(db.select(func.count()).select_from(Course.__table__)).scalar(),
        "total_sections": db.session.execute(db.select(func.count()).select_from(CourseSection.__table__)).scalar(),
        "total_enrollments": db.session.execute(db.select(func.count()).select_from(Enrollment.__table__)).scalar(),
    }
    courses = Course.query.order_by(Course.course_id.asc()).limit(10).all()
    sections = CourseSection.query.order_by(CourseSection.semester.desc(), CourseSection.section_id.asc()).limit(10).all()

    if _wants_json():
        return jsonify({
            "success": True,
            "stats": stats,
            "courses": [c.to_dict() for c in courses],
            "sections": [s.to_dict() for s in sections],
        }), 200
    return render_template("admin/semester_overview.html", stats=stats, courses=courses, sections=sections)


# --- Quản lý người dùng (Admin / Giảng viên / Sinh viên) ---

@admin_bp.route("/users", methods=["GET"])
@role_required("admin")
def list_users():
    role_filter = (request.args.get("role") or "").strip().lower()
    raw_status = (request.args.get("status") or "").strip()
    status_filter = _normalize_status(raw_status, default="") if raw_status else ""
    q = (request.args.get("q") or "").strip().lower()

    admins = Admin.query.order_by(Admin.user_id.desc()).all()
    lecturers = Lecturer.query.order_by(Lecturer.user_id.desc()).all()
    students = Student.query.order_by(Student.user_id.desc()).all()

    users = []
    for role_name, group in (("admin", admins), ("lecturer", lecturers), ("student", students)):
        if role_filter and role_filter != role_name:
            continue
        for item in group:
            if status_filter and (item.status or "").lower() != status_filter.lower():
                continue
            info = _format_user(item, role_name)
            if q and q not in f"{info['username']} {info['email']} {info['fullname']} {info['profile_id']}".lower():
                continue
            users.append(info)

    users.sort(key=lambda u: u["user_id"], reverse=True)

    if _wants_json():
        return jsonify({"success": True, "users": users, "count": len(users)}), 200
    return render_template(
        "admin/users.html",
        users=users,
        admins=admins,
        lecturers=lecturers,
        students=students,
        filters={"role": role_filter, "status": status_filter or raw_status, "q": q},
    )


@admin_bp.route("/users", methods=["POST"])
@role_required("admin")
def create_user():
    data = _get_data()
    role = (data.get("role") or "student").strip().lower()
    username = (data.get("username") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()
    fullname = (data.get("fullname") or data.get("full_name") or "").strip()
    status_val = _normalize_status(data.get("status"), default="Active")
    back = url_for("admin.list_users")

    if role not in {"admin", "lecturer", "student"}:
        return _reply(False, "Invalid role.", 400, back, "danger")
    if not status_val:
        return _reply(False, "Invalid status. Must be Active, Locked, or Pending.", 400, back, "danger")
    if not username or not email or not password:
        return _reply(False, "Username, email, and password are required.", 400, back, "danger")
    if len(username) > 50 or len(email) > 50:
        return _reply(False, "Username and email must not exceed 50 characters.", 400, back, "danger")

    usr_tbl = User.__table__
    dup_uid = db.session.execute(
        db.select(usr_tbl.c.userid).where(
            or_(func.lower(usr_tbl.c.username) == username.lower(), func.lower(usr_tbl.c.email) == email)
        )
    ).scalar_one_or_none()
    if dup_uid is not None:
        return _reply(False, "Username or email already exists.", 409, back, "danger")

    if role == "admin":
        aid = (data.get("admin_id") or data.get("profile_id") or "").strip() or _auto_id("admin", Admin, "adminid")
        if db.session.execute(db.select(Admin.__table__.c.adminid).where(Admin.__table__.c.adminid == aid)).scalar_one_or_none():
            return _reply(False, f"Admin ID '{aid}' already exists.", 409, back, "danger")
        user = Admin(
            admin_id=aid,
            role=(data.get("admin_role") or "Administrator").strip(),
            username=username,
            email=email,
            status=status_val,
        )
    elif role == "lecturer":
        lid = (data.get("lecturer_id") or data.get("profile_id") or "").strip() or _auto_id("lecturer", Lecturer, "lecturerid")
        if db.session.execute(db.select(Lecturer.__table__.c.lecturerid).where(Lecturer.__table__.c.lecturerid == lid)).scalar_one_or_none():
            return _reply(False, f"Lecturer ID '{lid}' already exists.", 409, back, "danger")
        user = Lecturer(lecturer_id=lid, fullname=fullname or username, username=username, email=email, status=status_val)
    else:
        phone, phone_err = _normalize_phone(data.get("phone", ""))
        if phone_err:
            return _reply(False, phone_err, 400, back, "danger")
        sid = (data.get("student_id") or data.get("profile_id") or "").strip() or _auto_id("student", Student, "studentid")
        if db.session.execute(db.select(Student.__table__.c.studentid).where(Student.__table__.c.studentid == sid)).scalar_one_or_none():
            return _reply(False, f"Student ID '{sid}' already exists.", 409, back, "danger")
        user = Student(student_id=sid, fullname=fullname or username, phone=phone, username=username, email=email, status=status_val)

    user.set_password(password)
    try:
        db.session.add(user)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _reply(False, "Could not create user.", 409, back, "danger")

    return _reply(True, f"Created {role} '{username}'.", 201, back, "success", {"user": _format_user(user, role)})


@admin_bp.route("/users/<int:user_id>/update", methods=["POST", "PUT"])
@role_required("admin")
def update_user(user_id):
    back = url_for("admin.list_users")
    user, role = _find_user(user_id)
    if not user:
        return _reply(False, "User not found.", 404, back, "danger")

    data = _get_data()
    new_email = (data.get("email") or "").strip().lower()
    if new_email and new_email != (user.email or "").lower():
        if len(new_email) > 50:
            return _reply(False, "Email must not exceed 50 characters.", 400, back, "danger")
        usr_tbl = User.__table__
        dup = db.session.execute(
            db.select(usr_tbl.c.userid).where(func.lower(usr_tbl.c.email) == new_email, usr_tbl.c.userid != user_id)
        ).scalar_one_or_none()
        if dup is not None:
            return _reply(False, "Email already in use.", 409, back, "danger")
        user.email = new_email

    if data.get("status"):
        status_val = _normalize_status(data["status"], default=None)
        if not status_val:
            return _reply(False, "Invalid status. Must be Active, Locked, or Pending.", 400, back, "danger")
        user.status = status_val

    if role in {"lecturer", "student"} and (data.get("fullname") or data.get("full_name")):
        user.fullname = (data.get("fullname") or data.get("full_name")).strip()
    if role == "student" and "phone" in data:
        phone, phone_err = _normalize_phone(data.get("phone", ""))
        if phone_err:
            return _reply(False, phone_err, 400, back, "danger")
        user.phone = phone
    if role == "admin" and data.get("admin_role"):
        user.role = str(data["admin_role"]).strip()
    if (data.get("password") or "").strip():
        user.set_password(data["password"].strip())

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _reply(False, "Could not update user.", 409, back, "danger")

    return _reply(True, f"Updated user '{user.username}'.", 200, back, "success", {"user": _format_user(user, role or "user")})


@admin_bp.route("/users/<int:user_id>/toggle-status", methods=["POST"])
@role_required("admin")
def toggle_user_status(user_id):
    back = url_for("admin.list_users")
    if session.get("user_id") == user_id:
        return _reply(False, "Cannot lock your own account.", 400, back, "warning")

    user, role = _find_user(user_id)
    if not user:
        return _reply(False, "User not found.", 404, back, "danger")

    user.status = "Locked" if (user.status or "").lower() == "active" else "Active"
    db.session.commit()
    return _reply(True, f"Changed '{user.username}' to {user.status}.", 200, back, "success", {"user": _format_user(user, role or "user")})


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST", "DELETE"])
@role_required("admin")
def delete_user(user_id):
    back = url_for("admin.list_users")
    if session.get("user_id") == user_id:
        return _reply(False, "Cannot delete your own account.", 400, back, "warning")

    user, _ = _find_user(user_id)
    if not user:
        return _reply(False, "User not found.", 404, back, "danger")

    username = user.username
    try:
        db.session.delete(user)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _reply(False, f"Cannot delete '{username}' because of related records.", 409, back, "danger")

    return _reply(True, f"Deleted user '{username}'.", 200, back, "success")


# --- Quản lý môn học (Courses) ---

@admin_bp.route("/courses", methods=["GET"])
@role_required("admin")
def list_courses():
    q = (request.args.get("q") or "").strip().lower()
    query = Course.query
    if q:
        query = query.filter(or_(func.lower(Course.course_id).contains(q), func.lower(Course.course_name).contains(q)))
    courses = query.order_by(Course.course_id.asc()).all()

    if _wants_json():
        return jsonify({"success": True, "courses": [c.to_dict(include_sections=True) for c in courses]}), 200
    return render_template("admin/course_catalog.html", courses=courses, q=q)


@admin_bp.route("/courses", methods=["POST"])
@role_required("admin")
def create_course():
    back = url_for("admin.list_courses")
    data = _get_data()
    cid = (data.get("course_id") or "").strip().upper()
    name = (data.get("course_name") or "").strip()
    try:
        credits = int(data.get("credits", 0))
    except (TypeError, ValueError):
        credits = 0

    if not cid or not name or credits <= 0:
        return _reply(False, "Course ID, name, and positive credits are required.", 400, back, "danger")
    if len(cid) > 50 or len(name) > 255:
        return _reply(False, "Course ID (max 50) or name (max 255) is too long.", 400, back, "danger")
    if db.session.execute(db.select(Course.__table__.c.courseid).where(Course.__table__.c.courseid == cid)).scalar_one_or_none():
        return _reply(False, f"Course '{cid}' already exists.", 409, back, "danger")

    course = Course(course_id=cid, course_name=name, credits=credits)
    try:
        db.session.add(course)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _reply(False, f"Could not create course '{cid}'.", 409, back, "danger")
    return _reply(True, f"Created course '{cid}'.", 201, back, "success", {"course": course.to_dict()})


@admin_bp.route("/courses/<string:course_id>/update", methods=["POST", "PUT"])
@role_required("admin")
def update_course(course_id):
    back = url_for("admin.list_courses")
    course = Course.query.filter_by(course_id=course_id).first()
    if not course:
        return _reply(False, "Course not found.", 404, back, "danger")

    data = _get_data()
    if (data.get("course_name") or "").strip():
        course.course_name = data["course_name"].strip()[:255]
    if "credits" in data and str(data["credits"]).strip() != "":
        try:
            credits = int(data["credits"])
            if credits <= 0:
                raise ValueError
            course.credits = credits
        except (TypeError, ValueError):
            return _reply(False, "Credits must be a positive integer.", 400, back, "danger")

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _reply(False, f"Could not update course '{course.course_id}'.", 409, back, "danger")
    return _reply(True, f"Updated course '{course.course_id}'.", 200, back, "success", {"course": course.to_dict()})


@admin_bp.route("/courses/<string:course_id>/delete", methods=["POST", "DELETE"])
@role_required("admin")
def delete_course(course_id):
    back = url_for("admin.list_courses")
    course = Course.query.filter_by(course_id=course_id).first()
    if not course:
        return _reply(False, "Course not found.", 404, back, "danger")

    try:
        db.session.delete(course)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _reply(False, f"Cannot delete '{course_id}' because sections are using it.", 409, back, "danger")

    return _reply(True, f"Deleted course '{course_id}'.", 200, back, "success")


# --- Quản lý lớp học phần & gán giảng viên / sinh viên ---

@admin_bp.route("/sections", methods=["GET"])
@role_required("admin")
def list_sections():
    semester = (request.args.get("semester") or "").strip()
    course_id = (request.args.get("course_id") or "").strip()
    lecturer_id = (request.args.get("lecturer_id") or "").strip()
    q = (request.args.get("q") or "").strip().lower()

    query = CourseSection.query
    if semester:
        query = query.filter(CourseSection.semester == semester)
    if course_id:
        query = query.filter(CourseSection.course_id == course_id)
    if lecturer_id:
        query = query.filter(CourseSection.lecturer_id == lecturer_id)
    if q:
        query = query.filter(
            or_(
                func.lower(CourseSection.section_id).contains(q),
                func.lower(CourseSection.section_name).contains(q),
                func.lower(CourseSection.room).contains(q),
            )
        )

    sections = query.order_by(CourseSection.semester.desc(), CourseSection.section_id.asc()).all()
    courses = Course.query.order_by(Course.course_id.asc()).all()
    lecturers = Lecturer.query.order_by(Lecturer.fullname.asc()).all()
    students = Student.query.order_by(Student.student_id.asc()).all()

    if _wants_json():
        return jsonify({"success": True, "sections": [s.to_dict(include_enrollments=True) for s in sections]}), 200
    return render_template(
        "admin/course_sections.html",
        sections=sections,
        courses=courses,
        lecturers=lecturers,
        students=students,
        filters={"semester": semester, "course_id": course_id, "lecturer_id": lecturer_id, "q": q},
    )


@admin_bp.route("/sections", methods=["POST"])
@role_required("admin")
def create_section():
    back = url_for("admin.list_sections")
    data = _get_data()
    sid = (data.get("section_id") or "").strip().upper()
    cid = (data.get("course_id") or "").strip()
    lid = (data.get("lecturer_id") or "").strip() or None
    name = (data.get("section_name") or "").strip()
    semester = (data.get("semester") or "").strip()
    room = (data.get("room") or "").strip() or None

    if not sid or not cid or not name or not semester:
        return _reply(False, "Section ID, course, name, and semester are required.", 400, back, "danger")
    if len(sid) > 50 or len(semester) > 50 or (room and len(room) > 50) or len(name) > 255:
        return _reply(False, "Field length exceeded maximum allowed characters.", 400, back, "danger")
    if db.session.execute(db.select(CourseSection.__table__.c.sectionid).where(CourseSection.__table__.c.sectionid == sid)).scalar_one_or_none():
        return _reply(False, f"Section '{sid}' already exists.", 409, back, "danger")
    if not db.session.execute(db.select(Course.__table__.c.courseid).where(Course.__table__.c.courseid == cid)).scalar_one_or_none():
        return _reply(False, f"Course '{cid}' not found.", 404, back, "danger")
    if lid and not db.session.execute(db.select(Lecturer.__table__.c.lecturerid).where(Lecturer.__table__.c.lecturerid == lid)).scalar_one_or_none():
        return _reply(False, f"Lecturer '{lid}' not found.", 404, back, "danger")

    section = CourseSection(section_id=sid, course_id=cid, lecturer_id=lid, section_name=name, semester=semester, room=room)
    try:
        db.session.add(section)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _reply(False, f"Could not create section '{sid}'.", 409, back, "danger")
    return _reply(True, f"Created section '{sid}'.", 201, back, "success", {"section": section.to_dict()})


@admin_bp.route("/sections/<string:section_id>/update", methods=["POST", "PUT"])
@role_required("admin")
def update_section(section_id):
    back = url_for("admin.list_sections")
    section = CourseSection.query.filter_by(section_id=section_id).first()
    if not section:
        return _reply(False, "Section not found.", 404, back, "danger")

    data = _get_data()
    if data.get("course_id"):
        cid = str(data["course_id"]).strip()
        if not db.session.execute(db.select(Course.__table__.c.courseid).where(Course.__table__.c.courseid == cid)).scalar_one_or_none():
            return _reply(False, f"Course '{cid}' not found.", 404, back, "danger")
        section.course_id = cid
    if "lecturer_id" in data:
        lid = (data.get("lecturer_id") or "").strip() or None
        if lid and not db.session.execute(db.select(Lecturer.__table__.c.lecturerid).where(Lecturer.__table__.c.lecturerid == lid)).scalar_one_or_none():
            return _reply(False, f"Lecturer '{lid}' not found.", 404, back, "danger")
        section.lecturer_id = lid
    if data.get("section_name"):
        section.section_name = str(data["section_name"]).strip()[:255]
    if data.get("semester"):
        section.semester = str(data["semester"]).strip()[:50]
    if "room" in data:
        room_val = (data.get("room") or "").strip()
        section.room = room_val[:50] if room_val else None

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _reply(False, f"Could not update section '{section.section_id}'.", 409, back, "danger")
    return _reply(True, f"Updated section '{section.section_id}'.", 200, back, "success", {"section": section.to_dict()})


@admin_bp.route("/sections/<string:section_id>/delete", methods=["POST", "DELETE"])
@role_required("admin")
def delete_section(section_id):
    back = url_for("admin.list_sections")
    section = CourseSection.query.filter_by(section_id=section_id).first()
    if not section:
        return _reply(False, "Section not found.", 404, back, "danger")

    try:
        db.session.delete(section)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return _reply(False, f"Cannot delete '{section_id}' because enrollments or sessions exist.", 409, back, "danger")

    return _reply(True, f"Deleted section '{section_id}'.", 200, back, "success")


@admin_bp.route("/sections/<string:section_id>/enrollments", methods=["GET"])
@role_required("admin")
def list_section_enrollments(section_id):
    if not db.session.execute(db.select(CourseSection.__table__.c.sectionid).where(CourseSection.__table__.c.sectionid == section_id)).scalar_one_or_none():
        return _reply(False, "Section not found.", 404, url_for("admin.list_sections"), "danger")

    items = Enrollment.query.filter_by(section_id=section_id).order_by(Enrollment.student_id.asc()).all()
    data = [
        {
            **e.to_dict(),
            "student_name": e.student.fullname if e.student else None,
            "student_email": e.student.email if e.student else None,
        }
        for e in items
    ]
    return jsonify({"success": True, "section_id": section_id, "enrollments": data}), 200


@admin_bp.route("/sections/<string:section_id>/enroll", methods=["POST"])
@role_required("admin")
def enroll_students(section_id):
    back = url_for("admin.list_sections")
    if not db.session.execute(db.select(CourseSection.__table__.c.sectionid).where(CourseSection.__table__.c.sectionid == section_id)).scalar_one_or_none():
        return _reply(False, "Section not found.", 404, back, "danger")

    data = _get_data()
    if isinstance(data.get("student_ids"), list):
        student_ids = [str(x).strip() for x in data["student_ids"] if str(x).strip()]
    else:
        student_ids = [x.strip() for x in request.form.getlist("student_ids") if x.strip()]
        single = (data.get("student_id") or "").strip()
        if single and single not in student_ids:
            student_ids.append(single)

    if not student_ids:
        return _reply(False, "Select at least one student.", 400, back, "danger")

    stu_tbl = Student.__table__
    enr_tbl = Enrollment.__table__
    added, skipped, missing = 0, 0, []
    for sid in student_ids:
        if not db.session.execute(db.select(stu_tbl.c.studentid).where(stu_tbl.c.studentid == sid)).scalar_one_or_none():
            missing.append(sid)
            continue
        if db.session.execute(db.select(enr_tbl.c.studentid).where(enr_tbl.c.studentid == sid, enr_tbl.c.sectionid == section_id)).scalar_one_or_none():
            skipped += 1
            continue
        db.session.add(Enrollment(student_id=sid, section_id=section_id, enroll_date=date.today()))
        added += 1

    db.session.commit()
    msg = f"Enrolled {added} student(s) into '{section_id}'."
    if skipped:
        msg += f" ({skipped} already enrolled)"
    if missing:
        msg += f" Not found: {', '.join(missing)}"
    return _reply(True, msg, 200, back, "success", {"added": added, "skipped": skipped, "missing": missing})


@admin_bp.route("/sections/<string:section_id>/enrollments/<string:student_id>/delete", methods=["POST", "DELETE"])
@role_required("admin")
def remove_enrollment(section_id, student_id):
    back = url_for("admin.list_sections")
    item = Enrollment.query.filter_by(section_id=section_id, student_id=student_id).first()
    if not item:
        return _reply(False, "Enrollment not found.", 404, back, "danger")

    db.session.delete(item)
    db.session.commit()
    return _reply(True, f"Removed '{student_id}' from '{section_id}'.", 200, back, "success")


# --- Import sinh viên từ CSV ---

@admin_bp.route("/students/csv-template", methods=["GET"])
@role_required("admin")
def download_student_csv_template():
    return Response(
        CSVService.generate_student_template(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=students_template.csv"},
    )


@admin_bp.route("/import-students", methods=["POST"])
@admin_bp.route("/sections/<string:section_id>/import-students", methods=["POST"])
@role_required("admin")
def import_students_csv(section_id=None):
    file = request.files.get("file") or request.files.get("csv_file")
    target_section = section_id or (request.form.get("section_id") or "").strip() or None
    default_pw = (request.form.get("default_password") or "").strip() or None
    back = url_for("admin.list_sections") if target_section else url_for("admin.list_users")

    if not file or not file.filename:
        return _reply(False, "Please upload a CSV file.", 400, back, "danger")
    if not file.filename.lower().endswith(".csv"):
        return _reply(False, "Only .csv files are supported.", 400, back, "danger")

    summary = CSVService.import_students(file.stream, section_id=target_section, default_password=default_pw)
    has_err = len(summary["errors"]) > 0
    msg = f"Done {summary['total_rows']} row(s): {summary['created_students']} created, {summary['enrolled']} enrolled."
    if has_err:
        msg += f" {len(summary['errors'])} error(s)."

    ok = summary["created_students"] > 0 or summary["enrolled"] > 0 or not has_err
    return _reply(ok, msg, 200 if ok else 400, back, "warning" if has_err else "success", {"summary": summary})