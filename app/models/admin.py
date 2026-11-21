from app.extensions.database import db
from app.models.user import User


class Admin(User):
    """Administrator model inheriting from User."""

    __tablename__ = "admins"

    admin_id = db.Column(
        "adminid",
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
            name="fk_admins_user",
        ),
        unique=True,
        nullable=False,
    )
    role = db.Column(
        "role",
        db.String(50),
        nullable=False,
        default="Administrator",
        server_default="Administrator",
    )

    __mapper_args__ = {
        "polymorphic_identity": "admin",
        "inherit_condition": (user_id == User.user_id),
    }

    @property
    def adminID(self) -> str:
        return self.admin_id

    @adminID.setter
    def adminID(self, value: str) -> None:
        self.admin_id = value

    def to_dict(self) -> dict:
        data = super().to_dict()
        data.update(
            {
                "admin_id": self.admin_id,
                "role": self.role,
            }
        )
        return data

    def __repr__(self) -> str:
        return f"<Admin {self.admin_id}: username={self.username}, role={self.role}>"


