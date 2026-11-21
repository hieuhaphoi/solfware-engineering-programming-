from app.extensions.database import db


class Student(db.Model):
    __tablename__ = "students"

    studentID = db.Column(
        "studentid",
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

    phone = db.Column(
        "phone",
        db.String(11),
    )

    user = db.relationship("User", foreign_keys=[userID])
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

    @property
    def student_id(self):
        return self.studentID

    @student_id.setter
    def student_id(self, value):
        self.studentID = value

    @property
    def user_id(self):
        return self.userID

    @user_id.setter
    def user_id(self, value):
        self.userID = value

