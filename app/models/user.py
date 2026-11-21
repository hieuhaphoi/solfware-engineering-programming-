import bcrypt
from app.extensions.database import db


class User(db.Model):
    """Base user account model (supports Joined Table Inheritance)."""

    VALID_STATUSES = ("ACTIVE", "INACTIVE", "LOCKED", "Active", "Inactive", "Locked")

    __tablename__ = "users"
    __table_args__ = (
        db.CheckConstraint(
            "UPPER(status) IN ('ACTIVE', 'INACTIVE', 'LOCKED')",
            name="ck_users_status",
        ),
    )

    user_id = db.Column(
        "userid",
        db.BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    username = db.Column(
        "username",
        db.String(50),
        unique=True,
        nullable=False,
    )
    email = db.Column(
        "email",
        db.String(255),
        unique=True,
        nullable=False,
    )
    password = db.Column(
        "password",
        db.String(255),
        nullable=False,
    )
    status = db.Column(
        "status",
        db.String(20),
        nullable=False,
        default="ACTIVE",
        server_default="ACTIVE",
    )

    __mapper_args__ = {
        "polymorphic_identity": "user",
    }

    @property
    def userID(self) -> int:
        return self.user_id

    @userID.setter
    def userID(self, value: int) -> None:
        self.user_id = value

    def set_password(self, raw_password: str) -> None:
        """Hash and store password using bcrypt."""
        if not raw_password:
            raise ValueError("Password cannot be empty.")
        salt = bcrypt.gensalt()
        self.password = bcrypt.hashpw(raw_password.encode("utf-8"), salt).decode("utf-8")

    def check_password(self, raw_password: str) -> bool:
        """Verify raw password against stored bcrypt hash (or fallback if plain)."""
        if not self.password or not raw_password:
            return False
        try:
            return bcrypt.checkpw(
                raw_password.encode("utf-8"),
                self.password.encode("utf-8"),
            )
        except ValueError:
            return self.password == raw_password

    def login(self, raw_password: str) -> bool:
        """Authenticate user credentials and verify active account status."""
        if self.status and self.status.upper() != "ACTIVE":
            return False
        return self.check_password(raw_password)

    def logout(self) -> bool:
        """Perform model-level logout action."""
        return True

    def reset_password(self, new_password: str) -> None:
        """Reset user password with a new raw password."""
        self.set_password(new_password)

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "email": self.email,
            "status": self.status,
        }

    def __repr__(self) -> str:
        return f"<User {self.user_id}: {self.username} ({self.status})>"

