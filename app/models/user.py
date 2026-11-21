from app.extensions.database import db

class User(db.Model):
    __tablename__ = "Users"

    userID = db.Column(db.BigInteger, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    email = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Active")