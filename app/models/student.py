from app.extensions.database import db
from app.models.user import User


class Student(User):
    """Student model inheriting from User."""

    __tablename__ = "students"

    student_id = db.Column(
        "studentid",
        db.String(50),
        primary_key=True,
    )
    user_id = db.Column(
        "userid",
        db.BigInteger,
        db.ForeignKey(
            "users.userid",
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_students_user",
        ),
        unique=True,
        nullable=False,
    )
    fullname = db.Column(
        "fullname",
        db.String(255),
        nullable=False,
    )
    phone = db.Column(
        "phone",
        db.String(15),
        nullable=True,
    )

    enrollments = db.relationship(
        "Enrollment",
        back_populates="student",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="select",
    )
    attendance_records = db.relationship(
        "AttendanceRecord",
        back_populates="student",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="select",
    )

    __mapper_args__ = {
        "polymorphic_identity": "student",
        "inherit_condition": (user_id == User.user_id),
    }

    @property
    def studentID(self) -> str:
        return self.student_id

    @studentID.setter
    def studentID(self, value: str) -> None:
        self.student_id = value

    @property
    def full_name(self) -> str:
        return self.fullname

    @full_name.setter
    def full_name(self, value: str) -> None:
        self.fullname = value

    def to_dict(self, include_enrollments: bool = False) -> dict:
        data = super().to_dict()
        data.update(
            {
                "student_id": self.student_id,
                "fullname": self.fullname,
                "phone": self.phone,
            }
        )
        if include_enrollments:
            data["enrollments"] = [
                enrollment.to_dict() for enrollment in self.enrollments
            ]
        return data

    def __repr__(self) -> str:
        return f"<Student {self.student_id}: {self.fullname}>"


