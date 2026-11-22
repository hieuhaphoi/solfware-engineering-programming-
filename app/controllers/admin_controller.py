from datetime import date

from flask import Blueprint, Response, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from app.extensions.database import db
from app.models import Admin, Course, CourseSection, Enrollment, Lecturer, Student, User
from app.services.auth_factory import role_required
from app.services.csv_service import CSVService

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _wants_json():
    return request.is_json or request.accept_mimetypes.best == "application/json"


def _get_data():
    return request.get_json(silent=True) or {} if request.is_json else request.form.to_dict()


def _reply(ok, msg, code=200, next_url=None, cat="info", extra=None):
    # Trả JSON nếu gọi bằng API, còn gọi từ form HTML thì flash + redirect
    if _wants_json():
        return jsonify({"success": ok, "message": msg, **(extra or {})}), code
    if msg:
        flash(msg, cat)
    return redirect(next_url or url_for("admin.dashboard"))


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


def _find_user(user_id):
    for role_name, model in (("admin", Admin), ("lecturer", Lecturer), ("student", Student)):
        item = model.query.filter_by(user_id=user_id).first()
        if item:
            return item, role_name
    user = User.query.filter_by(user_id=user_id).first()
    return user, ("user" if user else None)


def _auto_id(role_name, model, col_name):
    # Tự sinh mã AD0001 / GV0001 / SV0001 nếu admin bỏ trống ô mã
    prefix = {"admin": "AD", "lecturer": "GV", "student": "SV"}[role_name]
    num = model.query.count() + 1
    while True:
        code = f"{prefix}{num:04d}"
        if not model.query.filter(getattr(model, col_name) == code).first():
            return code
        num += 1


# --- Tổng quan học kỳ ---

@admin_bp.route("", methods=["GET"])
@admin_bp.route("/", methods=["GET"])
@role_required("admin")
def dashboard():
    stats = {
        "total_users": User.query.count(),
        "total_admins": Admin.query.count(),
        "total_lecturers": Lecturer.query.count(),
        "total_students": Student.query.count(),
        "total_courses": Course.query.count(),
        "total_sections": CourseSection.query.count(),
        "total_enrollments": Enrollment.query.count(),
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
    status_filter = (request.args.get("status") or "").strip().upper()
    q = (request.args.get("q") or "").strip().lower()

    admins = Admin.query.order_by(Admin.user_id.desc()).all()
    lecturers = Lecturer.query.order_by(Lecturer.user_id.desc()).all()
    students = Student.query.order_by(Student.user_id.desc()).all()

    users = []
    for role_name, group in (("admin", admins), ("lecturer", lecturers), ("student", students)):
        if role_filter and role_filter != role_name:
            continue
        for item in group:
            if status_filter and (item.status or "").upper() != status_filter:
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
        filters={"role": role_filter, "status": status_filter, "q": q},
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
    phone = (data.get("phone") or "").strip()[:15] or None
    status_val = (data.get("status") or "ACTIVE").strip().upper()
    back = url_for("admin.list_users")

    if role not in {"admin", "lecturer", "student"}:
        return _reply(False, "Invalid role.", 400, back, "danger")
    if not username or not email or not password:
        return _reply(False, "Username, email, and password are required.", 400, back, "danger")

    dup = User.query.filter(or_(func.lower(User.username) == username.lower(), func.lower(User.email) == email)).first()
    if dup:
        return _reply(False, "Username or email already exists.", 409, back, "danger")

    if role == "admin":
        aid = (data.get("admin_id") or data.get("profile_id") or "").strip() or _auto_id("admin", Admin, "admin_id")
        if Admin.query.filter_by(admin_id=aid).first():
            return _reply(False, f"Admin ID '{aid}' already exists.", 409, back, "danger")
        user = Admin(admin_id=aid, role=(data.get("admin_role") or "SYSTEM_ADMIN").strip(), username=username, email=email, status=status_val)
    elif role == "lecturer":
        lid = (data.get("lecturer_id") or data.get("profile_id") or "").strip() or _auto_id("lecturer", Lecturer, "lecturer_id")
        if Lecturer.query.filter_by(lecturer_id=lid).first():
            return _reply(False, f"Lecturer ID '{lid}' already exists.", 409, back, "danger")
        user = Lecturer(lecturer_id=lid, fullname=fullname or username, username=username, email=email, status=status_val)
    else:
        sid = (data.get("student_id") or data.get("profile_id") or "").strip() or _auto_id("student", Student, "student_id")
        if Student.query.filter_by(student_id=sid).first():
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
        if User.query.filter(func.lower(User.email) == new_email, User.user_id != user_id).first():
            return _reply(False, "Email already in use.", 409, back, "danger")
        user.email = new_email

    if data.get("status"):
        user.status = str(data["status"]).strip().upper()
    if role in {"lecturer", "student"} and (data.get("fullname") or data.get("full_name")):
        user.fullname = (data.get("fullname") or data.get("full_name")).strip()
    if role == "student" and "phone" in data:
        user.phone = (data.get("phone") or "").strip()[:15] or None
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

    user.status = "INACTIVE" if (user.status or "").upper() == "ACTIVE" else "ACTIVE"
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
    if Course.query.filter_by(course_id=cid).first():
        return _reply(False, f"Course '{cid}' already exists.", 409, back, "danger")

    course = Course(course_id=cid, course_name=name, credits=credits)
    db.session.add(course)
    db.session.commit()
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
        course.course_name = data["course_name"].strip()
    if "credits" in data and str(data["credits"]).strip() != "":
        try:
            credits = int(data["credits"])
            if credits <= 0:
                raise ValueError
            course.credits = credits
        except (TypeError, ValueError):
            return _reply(False, "Credits must be a positive integer.", 400, back, "danger")

    db.session.commit()
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
    if CourseSection.query.filter_by(section_id=sid).first():
        return _reply(False, f"Section '{sid}' already exists.", 409, back, "danger")
    if not Course.query.filter_by(course_id=cid).first():
        return _reply(False, f"Course '{cid}' not found.", 404, back, "danger")
    if lid and not Lecturer.query.filter_by(lecturer_id=lid).first():
        return _reply(False, f"Lecturer '{lid}' not found.", 404, back, "danger")

    section = CourseSection(section_id=sid, course_id=cid, lecturer_id=lid, section_name=name, semester=semester, room=room)
    db.session.add(section)
    db.session.commit()
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
        if not Course.query.filter_by(course_id=cid).first():
            return _reply(False, f"Course '{cid}' not found.", 404, back, "danger")
        section.course_id = cid
    if "lecturer_id" in data:
        lid = (data.get("lecturer_id") or "").strip() or None
        if lid and not Lecturer.query.filter_by(lecturer_id=lid).first():
            return _reply(False, f"Lecturer '{lid}' not found.", 404, back, "danger")
        section.lecturer_id = lid
    if data.get("section_name"):
        section.section_name = str(data["section_name"]).strip()
    if data.get("semester"):
        section.semester = str(data["semester"]).strip()
    if "room" in data:
        section.room = (data.get("room") or "").strip() or None

    db.session.commit()
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
    if not CourseSection.query.filter_by(section_id=section_id).first():
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
    if not CourseSection.query.filter_by(section_id=section_id).first():
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

    added, skipped, missing = 0, 0, []
    for sid in student_ids:
        if not Student.query.filter_by(student_id=sid).first():
            missing.append(sid)
            continue
        if Enrollment.query.filter_by(student_id=sid, section_id=section_id).first():
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