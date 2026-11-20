
from app.extensions.database import db


class Course(db.Model):
    """Course catalog model."""

    __tablename__ = "courses"
    __table_args__ = (
        db.CheckConstraint("credits > 0", name="ck_courses_credits_positive"),
    )

    course_id = db.Column("courseid", db.String(50), primary_key=True)
    course_name = db.Column("coursename", db.String(255), nullable=False)
    credits = db.Column("credits", db.Integer, nullable=False)

    # 1-N with CourseSection (ON DELETE RESTRICT)
    sections = db.relationship(
        "CourseSection",
        back_populates="course",
        passive_deletes="all",
        lazy="select",
    )

    def to_dict(self, include_sections: bool = False) -> dict:
        data = {
            "course_id": self.course_id,
            "course_name": self.course_name,
            "credits": self.credits,
        }
        if include_sections:
            data["sections"] = [section.to_dict() for section in self.sections]
        return data

    def __repr__(self) -> str:
        return f"<Course {self.course_id}: {self.course_name} ({self.credits} credits)>"