from app.extensions.database import db


class CourseSection(db.Model):
    """Course section model for a specific semester."""

    __tablename__ = "course_sections"
    __table_args__ = (
        db.Index("idx_course_sections_course", "courseid"),
        db.Index("idx_course_sections_lecturer", "lecturerid"),
    )

    section_id = db.Column("sectionid", db.String(50), primary_key=True)
    course_id = db.Column(
        "courseid",
        db.String(50),
        db.ForeignKey(
            "courses.courseid",
            ondelete="RESTRICT",
            onupdate="CASCADE",
            name="fk_sections_course",
        ),
        nullable=False,
    )
    lecturer_id = db.Column(
        "lecturerid",
        db.String(50),
        db.ForeignKey(
            "lecturers.lecturerid",
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_sections_lecturer",
        ),
        nullable=True,
    )
    section_name = db.Column("sectionname", db.String(255), nullable=False)
    semester = db.Column("semester", db.String(50), nullable=False)
    room = db.Column("room", db.String(50), nullable=True)

    # Relationships
    course = db.relationship("Course", back_populates="sections")
    lecturer = db.relationship(
        "Lecturer",
        back_populates="sections",
        foreign_keys=[lecturer_id],
    )
    enrollments = db.relationship(
        "Enrollment",
        back_populates="section",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="select",
    )
    attendance_sessions = db.relationship(
        "AttendanceSession",
        back_populates="section",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="select",
    )

    def to_dict(self, include_enrollments: bool = False) -> dict:
        data = {
            "section_id": self.section_id,
            "course_id": self.course_id,
            "lecturer_id": self.lecturer_id,
            "section_name": self.section_name,
            "semester": self.semester,
            "room": self.room,
        }
        if include_enrollments:
            data["enrollments"] = [
                enrollment.to_dict() for enrollment in self.enrollments
            ]
        return data

    def __repr__(self) -> str:
        return f"<CourseSection {self.section_id}: {self.section_name} ({self.semester})>"