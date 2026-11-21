from app.extensions.database import db
from app.models.user import User


class Lecturer(User):
    """Lecturer model inheriting from User."""

    __tablename__ = "lecturers"

    lecturer_id = db.Column(
        "lecturerid",
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
            name="fk_lecturers_user",
        ),
        unique=True,
        nullable=False,
    )
    fullname = db.Column(
        "fullname",
        db.String(255),
        nullable=False,
    )

    sections = db.relationship(
        "CourseSection",
        back_populates="lecturer",
        lazy="select",
    )

    __mapper_args__ = {
        "polymorphic_identity": "lecturer",
        "inherit_condition": (user_id == User.user_id),
    }

    @property
    def lecturerID(self) -> str:
        return self.lecturer_id

    @lecturerID.setter
    def lecturerID(self, value: str) -> None:
        self.lecturer_id = value

    @property
    def full_name(self) -> str:
        return self.fullname

    @full_name.setter
    def full_name(self, value: str) -> None:
        self.fullname = value

    def to_dict(self, include_sections: bool = False) -> dict:
        data = super().to_dict()
        data.update(
            {
                "lecturer_id": self.lecturer_id,
                "fullname": self.fullname,
            }
        )
        if include_sections:
            data["sections"] = [section.to_dict() for section in self.sections]
        return data

    def __repr__(self) -> str:
        return f"<Lecturer {self.lecturer_id}: {self.fullname}>"


