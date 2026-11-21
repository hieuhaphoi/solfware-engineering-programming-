from app.extensions.database import db


class User(db.Model):
    __tablename__ = "users"

    userID = db.Column("userid", db.BigInteger, primary_key=True, autoincrement=True)
    username = db.Column("username", db.String(50), unique=True, nullable=False)
    email = db.Column("email", db.String(50), unique=True, nullable=False)
    password = db.Column("password", db.String(255), nullable=False)
    status = db.Column("status", db.String(20), nullable=False, default="Active")

    @property
    def user_id(self):
        return self.userID

    @user_id.setter
    def user_id(self, value):
        self.userID = value
