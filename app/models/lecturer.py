from app.extensions.database import db


class Lecturer(db.Model):
    __tablename__ = "lecturers"

    lecturerID = db.Column(
        "lecturerid",
        db.String(50),
        primary_key=True,
    )

    userID = db.Column(
        "userid",
        db.BigInteger,
        db.ForeignKey("users.userid", ondelete="CASCADE", onupdate="CASCADE"),
        unique=True,
        nullable=False,
    )

    fullname = db.Column(
        "fullname",
        db.String(255),
        nullable=False,
    )

    user = db.relationship("User", foreign_keys=[userID])
    sections = db.relationship(
        "CourseSection",
        back_populates="lecturer",
        lazy="select",
    )

    @property
    def lecturer_id(self):
        return self.lecturerID

    @lecturer_id.setter
    def lecturer_id(self, value):
        self.lecturerID = value

    @property
    def user_id(self):
        return self.userID

    @user_id.setter
    def user_id(self, value):
        self.userID = value

