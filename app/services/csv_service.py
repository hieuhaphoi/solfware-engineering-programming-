import csv
import io
import re
from datetime import date

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from app.extensions.database import db
from app.models import CourseSection, Enrollment, Student, User

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Map tên cột phổ biến để file xuất từ Excel tiếng Việt hay Anh đều đọc được
COLUMN_MAP = {
    "student_id": {"student_id", "studentid", "mssv", "ma_sv", "masv", "id"},
    "fullname": {"fullname", "full_name", "ho_ten", "hoten", "name", "ten"},
    "email": {"email", "mail"},
    "phone": {"phone", "sdt", "so_dien_thoai", "phone_number"},
    "username": {"username", "user_name", "tai_khoan"},
    "password": {"password", "mat_khau"},
    "section_id": {"section_id", "sectionid", "ma_lop", "lop_hoc_phan"},
}


class CSVService:
    DEFAULT_PASSWORD = "Student@123"
    TEMPLATE_HEADERS = ["student_id", "fullname", "email", "phone", "username", "password"]

    @staticmethod
    def _clean_header(name):
        key = re.sub(r"[\s\-]+", "_", (name or "").strip().lower())
        for standard, aliases in COLUMN_MAP.items():
            if key in aliases:
                return standard
        return key

    @staticmethod
    def _read_text(file_or_text):
        if isinstance(file_or_text, str):
            return file_or_text
        raw = file_or_text.read() if hasattr(file_or_text, "read") else bytes(file_or_text)
        if isinstance(raw, str):
            return raw
        for enc in ("utf-8-sig", "utf-8", "cp1258", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    @classmethod
    def parse_csv(cls, file_or_text):
        text = cls._read_text(file_or_text).strip()
        if not text:
            return []

        # Tự đoán dấu phẩy hoặc chấm phẩy (Excel hay xuất bằng ;)
        try:
            dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;")
        except csv.Error:
            dialect = csv.excel

        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
        if not reader.fieldnames:
            return []

        rows = []
        for raw_row in reader:
            row = {cls._clean_header(k): (v or "").strip() for k, v in raw_row.items() if k}
            if any(row.values()):
                rows.append(row)
        return rows

    @classmethod
    def generate_student_template(cls):
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(cls.TEMPLATE_HEADERS)
        writer.writerow(["SV001", "Nguyen Van A", "sv001@uth.edu.vn", "0901234567", "sv001", "Student@123"])
        writer.writerow(["SV002", "Tran Thi B", "sv002@uth.edu.vn", "0909876543", "sv002", ""])
        return buf.getvalue()

    @classmethod
    def import_students(cls, file_or_text, section_id=None, default_password=None):
        rows = cls.parse_csv(file_or_text)
        fallback_pw = (default_password or cls.DEFAULT_PASSWORD).strip() or cls.DEFAULT_PASSWORD
        target_section = (section_id or "").strip() or None

        result = {
            "total_rows": len(rows),
            "created_students": 0,
            "existing_students": 0,
            "enrolled": 0,
            "already_enrolled": 0,
            "errors": [],
            "default_password": fallback_pw,
        }

        if not rows:
            result["errors"].append({"row": 0, "error": "CSV file is empty or invalid."})
            return result

        if target_section and not CourseSection.query.filter_by(section_id=target_section).first():
            result["errors"].append({"row": 0, "error": f"Section '{target_section}' not found."})
            return result

        for idx, row in enumerate(rows, start=2):
            sid = row.get("student_id", "").strip()
            fullname = row.get("fullname", "").strip()
            email = row.get("email", "").strip().lower()
            phone = row.get("phone", "").strip()[:15] or None
            username = (row.get("username", "").strip() or sid).lower()
            password = row.get("password", "").strip() or fallback_pw
            sec_id = target_section or (row.get("section_id", "").strip() or None)

            if not sid:
                result["errors"].append({"row": idx, "error": "Missing student_id."})
                continue

            # Dùng savepoint để dòng nào lỗi thì bỏ qua dòng đó, không rollback cả file
            try:
                with db.session.begin_nested():
                    student = Student.query.filter_by(student_id=sid).first()
                    if not student and email:
                        student = Student.query.filter(func.lower(Student.email) == email).first()

                    if student:
                        result["existing_students"] += 1
                    else:
                        if not fullname or not email:
                            result["errors"].append({"row": idx, "error": f"Student '{sid}' needs fullname and email."})
                            continue
                        if not EMAIL_RE.match(email):
                            result["errors"].append({"row": idx, "error": f"Invalid email '{email}'."})
                            continue

                        existed_user = User.query.filter(
                            or_(func.lower(User.username) == username, func.lower(User.email) == email)
                        ).first()
                        if existed_user:
                            result["errors"].append({"row": idx, "error": f"Duplicate username/email at '{sid}'."})
                            continue

                        student = Student(
                            student_id=sid,
                            username=username,
                            email=email,
                            fullname=fullname,
                            phone=phone,
                            status="ACTIVE",
                        )
                        student.set_password(password)
                        db.session.add(student)
                        db.session.flush()
                        result["created_students"] += 1

                    # Nếu có truyền mã lớp thì gán luôn sinh viên vào lớp đó
                    if sec_id:
                        if not CourseSection.query.filter_by(section_id=sec_id).first():
                            result["errors"].append({"row": idx, "error": f"Section '{sec_id}' not found."})
                            continue
                        existed_enroll = Enrollment.query.filter_by(student_id=student.student_id, section_id=sec_id).first()
                        if existed_enroll:
                            result["already_enrolled"] += 1
                        else:
                            db.session.add(Enrollment(student_id=student.student_id, section_id=sec_id, enroll_date=date.today()))
                            result["enrolled"] += 1
            except IntegrityError:
                result["errors"].append({"row": idx, "error": f"Database conflict for '{sid}'."})
            except Exception as err:
                result["errors"].append({"row": idx, "error": str(err)})

        db.session.commit()
        return result