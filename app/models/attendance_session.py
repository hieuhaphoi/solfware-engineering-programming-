from datetime import date, datetime
from app.extensions.database import db


class AttendanceSession(db.Model):
    """Attendance session model for a specific class meeting."""

    __tablename__ = "attendance_sessions"
    __table_args__ = (
        db.Index("idx_attendance_sessions_section", "sectionid"),
    )

    session_id = db.Column(
        "sessionid",
        db.BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    section_id = db.Column(
        "sectionid",
        db.String(50),
        db.ForeignKey(
            "course_sections.sectionid",
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_sessions_section",
        ),
        nullable=False,
    )
    session_date = db.Column(
        "sessiondate",
        db.Date,
        nullable=False,
        default=date.today,
        server_default=db.func.current_date(),
    )
    pin_code = db.Column("pincode", db.String(10), nullable=False)
    start_time = db.Column(
        "starttime",
        db.DateTime,
        nullable=False,
        default=datetime.now,
        server_default=db.func.current_timestamp(),
    )
    end_time = db.Column("endtime", db.DateTime, nullable=False)

    section = db.relationship("CourseSection", foreign_keys=[section_id])

    records = db.relationship(
        "AttendanceRecord",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="select",
    )

    def to_dict(self, include_records: bool = False) -> dict:
        data = {
            "session_id": self.session_id,
            "section_id": self.section_id,
            "session_date": (
                self.session_date.isoformat() if self.session_date else None
            ),
            "pin_code": self.pin_code,
            "start_time": (
                self.start_time.isoformat() if self.start_time else None
            ),
            "end_time": self.end_time.isoformat() if self.end_time else None,
        }
        if include_records:
            data["records"] = [record.to_dict() for record in self.records]
        return data

    def __repr__(self) -> str:
        return (
            f"<AttendanceSession {self.session_id}: "
            f"section={self.section_id} ({self.session_date})>"
        )
