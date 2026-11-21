from app.extensions.database import db


class Admin(db.Model):
    __tablename__ = "admins"

    adminID = db.Column("adminid", db.String(50), primary_key=True)

    userID = db.Column(
        "userid",
        db.BigInteger,
        db.ForeignKey("users.userid", ondelete="CASCADE", onupdate="CASCADE"),
        unique=True,
        nullable=False,
    )

    role = db.Column(
        "role",
        db.String(50),
        nullable=False,
        default="Administrator",
    )

    user = db.relationship("User", foreign_keys=[userID])

    @property
    def admin_id(self):
        return self.adminID

    @admin_id.setter
    def admin_id(self, value):
        self.adminID = value

    @property
    def user_id(self):
        return self.userID

    @user_id.setter
    def user_id(self, value):
        self.userID = value

