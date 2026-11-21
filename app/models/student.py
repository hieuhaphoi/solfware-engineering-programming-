from app.extensions.database import db

class Student(db.Model):
    __tablename__ = "Students"

    studentID = db.Column(
        db.String(50),
        primary_key=True
    )

    userID = db.Column(
        db.BigInteger,
        db.ForeignKey("Users.userID"),
        unique=True,
        nullable=False
    )

    fullname = db.Column(
        db.String(255),
        nullable=False
    )

    phone = db.Column(
        db.String(11)
    )
