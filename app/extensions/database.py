from datetime import date, datetime, timedelta
import logging
from typing import Optional
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import create_engine, text
from app.config import Config

logger = logging.getLogger(__name__)

# SQLAlchemy ORM instance for Flask MVC Models
db = SQLAlchemy()


def configure_database_uri(app) -> None:
    """Check if the configured database is reachable; if PostgreSQL fails, fall back to local SQLite."""
    uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri or uri.startswith("sqlite"):
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
        return

    try:
        test_engine = create_engine(uri, connect_args={"connect_timeout": 2})
        with test_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        test_engine.dispose()
    except Exception:
        fallback_uri = app.config.get("SQLITE_FALLBACK_URI") or getattr(Config, "SQLITE_FALLBACK_URI", "sqlite:///attendly_local.db")
        logger.info("Using pre-seeded local SQLite database at %s", fallback_uri)
        app.config["SQLALCHEMY_DATABASE_URI"] = fallback_uri
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}


def ensure_tables_and_seed() -> None:
    """Create tables and seed default Figma sample data if the database is empty."""
    from app.models import (
        Admin,
        AttendanceRecord,
        AttendanceSession,
        Course,
        CourseSection,
        Enrollment,
        Lecturer,
        Student,
        User,
    )
    from app.services.session_manager import SessionManager

    db.create_all()

    existing_users = db.session.execute(db.select(db.func.count()).select_from(User.__table__)).scalar() or 0
    if existing_users > 0:
        return

    default_pw_hash = "$2b$12$zOihdBuK6LPvep/KmYtucOqAp90haRRNuUO3unVWFbijU2Lyg.LyS"  # 'password123'

    # 1. Admin: jamie.lee
    admin = Admin(
        admin_id="A-1001",
        role="Administrator",
        username="jamie.lee",
        email="jamie.lee@westbridge.edu",
        password=default_pw_hash,
        status="Active",
    )
    # 2. Lecturers: Dr. Maya Chen & Dr. Elias Reed
    lec1 = Lecturer(
        lecturer_id="L-1001",
        fullname="Dr. Maya Chen",
        username="maya.chen",
        email="maya.chen@westbridge.edu",
        password=default_pw_hash,
        status="Active",
    )
    lec2 = Lecturer(
        lecturer_id="L-1002",
        fullname="Dr. Elias Reed",
        username="elias.reed",
        email="elias.reed@westbridge.edu",
        password=default_pw_hash,
        status="Active",
    )
    # 3. Students: Alex Morgan, Jordan Lee, Samira Patel
    stu1 = Student(
        student_id="2026001",
        fullname="Alex Morgan",
        phone="0901234567",
        username="alex.morgan",
        email="alex.morgan@westbridge.edu",
        password=default_pw_hash,
        status="Active",
    )
    stu2 = Student(
        student_id="2026002",
        fullname="Jordan Lee",
        phone="0901234568",
        username="jordan.lee",
        email="jordan.lee@westbridge.edu",
        password=default_pw_hash,
        status="Active",
    )
    stu3 = Student(
        student_id="2026003",
        fullname="Samira Patel",
        phone="0901234569",
        username="samira.patel",
        email="samira.patel@westbridge.edu",
        password=default_pw_hash,
        status="Active",
    )
    db.session.add_all([admin, lec1, lec2, stu1, stu2, stu3])
    db.session.flush()

    # 4. Courses
    courses = [
        Course(course_id="CS201", course_name="Database Systems", credits=3),
        Course(course_id="CS105", course_name="Programming Fundamentals", credits=3),
        Course(course_id="MA202", course_name="Discrete Mathematics", credits=4),
        Course(course_id="SE301", course_name="Software Engineering", credits=4),
    ]
    db.session.add_all(courses)
    db.session.flush()

    # 5. Course Sections
    sections = [
        CourseSection(
            section_id="CS201-A",
            course_id="CS201",
            lecturer_id="L-1001",
            section_name="Section A",
            semester="Fall 2026",
            room="Room B-204",
        ),
        CourseSection(
            section_id="CS105-B",
            course_id="CS105",
            lecturer_id="L-1001",
            section_name="Section B",
            semester="Fall 2026",
            room="Room A-102",
        ),
        CourseSection(
            section_id="MA202-A",
            course_id="MA202",
            lecturer_id="L-1002",
            section_name="Section A",
            semester="Fall 2026",
            room="Room C-105",
        ),
    ]
    db.session.add_all(sections)
    db.session.flush()

    # 6. Enrollments
    enroll_date = date(2026, 8, 24)
    enrollments = [
        Enrollment(student_id="2026001", section_id="CS201-A", enroll_date=enroll_date),
        Enrollment(student_id="2026002", section_id="CS201-A", enroll_date=enroll_date),
        Enrollment(student_id="2026003", section_id="CS201-A", enroll_date=enroll_date),
        Enrollment(student_id="2026001", section_id="CS105-B", enroll_date=enroll_date),
        Enrollment(student_id="2026001", section_id="MA202-A", enroll_date=enroll_date),
    ]
    db.session.add_all(enrollments)
    db.session.flush()

    # 7. Active Attendance Session & Records (PIN 482761 open for 5 minutes)
    now = datetime.now()
    sess1 = AttendanceSession(
        section_id="CS201-A",
        session_date=now.date(),
        pin_code="482761",
        start_time=now,
        end_time=now + timedelta(minutes=5),
    )
    db.session.add(sess1)
    db.session.flush()

    # Register PIN 482761 in Redis/Memory cache as well
    try:
        from app.extensions.redis_client import redis_client
        redis_client.set_with_ttl(f"{SessionManager.KEY_PREFIX}:{sess1.session_id}", "482761", 300)
    except Exception:
        pass

    records = [
        AttendanceRecord(student_id="2026001", session_id=sess1.session_id, check_time=None, status="ABSENT", absent=True),
        AttendanceRecord(student_id="2026002", session_id=sess1.session_id, check_time=now, status="PRESENT", absent=False),
        AttendanceRecord(student_id="2026003", session_id=sess1.session_id, check_time=now, status="LATE", absent=False),
    ]
    db.session.add_all(records)
    db.session.commit()
    logger.info("Initialized database and seeded default Figma demo data.")


class DatabaseConnectionSingleton:
    """
    <<Singleton>> Database Connection Pool Manager for PostgreSQL (Psycopg 3).
    Ensures only a single shared connection pool instance is instantiated
    throughout the application lifecycle (Section V.2 - Singleton Pattern).
    """

    _instance: Optional["DatabaseConnectionSingleton"] = None
    _connection_pool: Optional[ConnectionPool] = None

    def __new__(cls) -> "DatabaseConnectionSingleton":
        if cls._instance is None:
            cls._instance = super(DatabaseConnectionSingleton, cls).__new__(cls)
        return cls._instance

    def init_pool(self, min_size: int = 1, max_size: int = 20) -> None:
        """Initialize the PostgreSQL connection pool if not already created."""
        if self._connection_pool is None:
            conninfo = (
                f"host={Config.DB_HOST} "
                f"port={Config.DB_PORT} "
                f"dbname={Config.DB_NAME} "
                f"user={Config.DB_USER} "
                f"password={Config.DB_PASSWORD}"
            )
            self._connection_pool = ConnectionPool(
                conninfo=conninfo,
                min_size=min_size,
                max_size=max_size,
                kwargs={"row_factory": dict_row},
            )

    def get_connection(self):
        """Acquire a connection from the shared PostgreSQL pool."""
        if self._connection_pool is None:
            self.init_pool()
        return self._connection_pool.getconn()

    def release_connection(self, conn) -> None:
        """Return a connection back to the pool."""
        if self._connection_pool is not None and conn is not None:
            self._connection_pool.putconn(conn)

    def close_all(self) -> None:
        """Close all connections in the pool on application shutdown."""
        if self._connection_pool is not None:
            self._connection_pool.close()
            self._connection_pool = None


# Shared Singleton instance
pg_manager = DatabaseConnectionSingleton()
