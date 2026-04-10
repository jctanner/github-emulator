from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    privacy: Mapped[str] = mapped_column(String, default="closed")  # "closed" or "secret"
    permission: Mapped[str] = mapped_column(String, default="pull")  # "pull", "push", or "admin"

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    organization = relationship("Organization", back_populates="teams", lazy="selectin")
    members = relationship("TeamMembership", back_populates="team", lazy="selectin")
    repos = relationship("TeamRepo", back_populates="team", lazy="selectin")


class TeamMembership(Base):
    __tablename__ = "team_memberships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String, default="member")  # "member" or "maintainer"

    # Relationships
    team = relationship("Team", back_populates="members", lazy="selectin")
    user = relationship("User", lazy="selectin")


class TeamRepo(Base):
    __tablename__ = "team_repos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), nullable=False)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id"), nullable=False)
    permission: Mapped[str] = mapped_column(String, default="pull")  # "pull", "push", or "admin"

    # Relationships
    team = relationship("Team", back_populates="repos", lazy="selectin")
    repository = relationship("Repository", lazy="selectin")
