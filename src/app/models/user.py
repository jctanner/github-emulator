from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    login: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String, nullable=True)
    bio: Mapped[str | None] = mapped_column(String, nullable=True)
    company: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    blog: Mapped[str | None] = mapped_column(String, nullable=True)
    twitter_username: Mapped[str | None] = mapped_column(String, nullable=True)
    hashed_password: Mapped[str] = mapped_column(String, nullable=False)
    site_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    type: Mapped[str] = mapped_column(String, default="User")

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    tokens = relationship("PersonalAccessToken", back_populates="user", lazy="selectin")
    repositories = relationship("Repository", back_populates="owner", lazy="selectin")
    owned_orgs = relationship(
        "Organization",
        secondary="org_memberships",
        primaryjoin="User.id == OrgMembership.user_id",
        secondaryjoin="and_(OrgMembership.org_id == Organization.id, OrgMembership.role == 'admin')",
        viewonly=True,
        lazy="selectin",
    )
