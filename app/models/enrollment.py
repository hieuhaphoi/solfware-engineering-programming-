from datetime import date
from app.extensions.database import db


class Enrollment(db.Model):
    """Student-to-section enrollment model (N-N join table)."""

    __tablename__ = "enrollments"
    __table_args__ = (
        db.Index("idx_enrollments_section", "sectionid"),
    )

    student_id = db.Column(
        "studentid",
        db.String(50),
        db.ForeignKey(
            "students.studentid",
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_enrollments_student",
        ),
        primary_key=True,
        nullable=False,
    )
    section_id = db.Column(
        "sectionid",
        db.String(50),
        db.ForeignKey(
            "course_sections.sectionid",
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_enrollments_section",
        ),
        primary_key=True,
        nullable=False,
    )
    enroll_date = db.Column(
        "enrolldate",
        db.Date,
        nullable=False,
        default=date.today,
        server_default=db.func.current_date(),
    )

    # Relationships
    section = db.relationship("CourseSection", back_populates="enrollments")
    student = db.relationship(
        "Student",
        back_populates="enrollments",
        foreign_keys=[student_id],
    )

    def to_dict(self) -> dict:
        return {
            "student_id": self.student_id,
            "section_id": self.section_id,
            "enroll_date": self.enroll_date.isoformat() if self.enroll_date else None,
        }

    def __repr__(self) -> str:
        return f"<Enrollment student={self.student_id}, section={self.section_id}>"