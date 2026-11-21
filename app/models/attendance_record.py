from datetime import datetime
from typing import Optional
from app.extensions.database import db


class AttendanceRecord(db.Model):
    """Student attendance result model per session."""

    VALID_STATUSES = ("PRESENT", "ABSENT", "LATE", "EXCUSED")

    __tablename__ = "attendance_records"
    __table_args__ = (
        db.CheckConstraint(
            "status IN ('PRESENT', 'ABSENT', 'LATE', 'EXCUSED')",
            name="ck_attendance_records_status",
        ),
        db.UniqueConstraint(
            "studentid",
            "sessionid",
            name="uq_student_session",
        ),
        db.Index("idx_attendance_records_session", "sessionid"),
        db.Index("idx_attendance_records_student", "studentid"),
    )

    record_id = db.Column(
        "recordid",
        db.BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    student_id = db.Column(
        "studentid",
        db.String(50),
        db.ForeignKey(
            "students.studentid",
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_records_student",
        ),
        nullable=False,
    )
    session_id = db.Column(
        "sessionid",
        db.BigInteger,
        db.ForeignKey(
            "attendance_sessions.sessionid",
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_records_session",
        ),
        nullable=False,
    )
    check_time = db.Column("checktime", db.DateTime, nullable=True)
    status = db.Column(
        "status",
        db.String(20),
        nullable=False,
        default="ABSENT",
        server_default="ABSENT",
    )
    absent = db.Column(
        "absent",
        db.Boolean,
        nullable=False,
        default=True,
        server_default=db.text("TRUE"),
    )

    session = db.relationship("AttendanceSession", back_populates="records")

    def update_status(
        self,
        new_status: str,
        check_time: Optional[datetime] = None,
    ) -> None:
        """Update attendance status and sync the absent flag (Class Diagram 2.8)."""
        normalized = new_status.upper()
        if normalized not in self.VALID_STATUSES:
            raise ValueError(
                f"Invalid status '{new_status}'. Must be one of {self.VALID_STATUSES}."
            )
        self.status = normalized
        self.absent = normalized == "ABSENT"
        if check_time is not None:
            self.check_time = check_time
        elif normalized in ("PRESENT", "LATE") and self.check_time is None:
            self.check_time = datetime.now()

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "student_id": self.student_id,
            "session_id": self.session_id,
            "check_time": (
                self.check_time.isoformat() if self.check_time else None
            ),
            "status": self.status,
            "absent": self.absent,
        }

    def __repr__(self) -> str:
        return (
            f"<AttendanceRecord {self.record_id}: "
            f"student={self.student_id}, session={self.session_id}, status={self.status}>"
        )
