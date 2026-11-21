from app.extensions.database import db

class Admin(db.Model):
    __tablename__ = "Admins"

    adminID = db.Column(db.String(50), primary_key=True)

    userID = db.Column(
        db.BigInteger,
        db.ForeignKey("Users.userID"),
        unique=True,
        nullable=False
    )

    role = db.Column(
        db.String(50),
        nullable=False,
        default="Administrator"
    )
