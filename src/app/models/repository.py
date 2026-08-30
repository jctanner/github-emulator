from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    owner_type: Mapped[str] = mapped_column(String, default="User")  # "User" or "Organization"
    name: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    private: Mapped[bool] = mapped_column(Boolean, default=False)
    fork: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("repositories.id"), nullable=True
    )
    default_branch: Mapped[str] = mapped_column(String, default="main")
    disk_path: Mapped[str | None] = mapped_column(String, nullable=True)

    has_issues: Mapped[bool] = mapped_column(Boolean, default=True)
    has_wiki: Mapped[bool] = mapped_column(Boolean, default=True)
    has_projects: Mapped[bool] = mapped_column(Boolean, default=True)
    has_downloads: Mapped[bool] = mapped_column(Boolean, default=True)
    has_pages: Mapped[bool] = mapped_column(Boolean, default=False)
    has_discussions: Mapped[bool] = mapped_column(Boolean, default=False)

    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    visibility: Mapped[str] = mapped_column(String, default="public")
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    homepage: Mapped[str | None] = mapped_column(String, nullable=True)
    topics: Mapped[list] = mapped_column(JSON, default=list)

    allow_forking: Mapped[bool] = mapped_column(Boolean, default=True)
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)
    web_commit_signoff_required: Mapped[bool] = mapped_column(Boolean, default=False)

    forks_count: Mapped[int] = mapped_column(Integer, default=0)
    stargazers_count: Mapped[int] = mapped_column(Integer, default=0)
    watchers_count: Mapped[int] = mapped_column(Integer, default=0)
    open_issues_count: Mapped[int] = mapped_column(Integer, default=0)
    size: Mapped[int] = mapped_column(Integer, default=0)
    next_issue_number: Mapped[int] = mapped_column(Integer, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    owner = relationship("User", back_populates="repositories", lazy="selectin")
    parent = relationship("Repository", remote_side="Repository.id", lazy="selectin")
    collaborators = relationship(
        "Collaborator", back_populates="repository", lazy="selectin",
        cascade="all, delete-orphan",
    )
    stars = relationship(
        "StarredRepo", back_populates="repository", lazy="selectin",
        cascade="all, delete-orphan",
    )
    branches = relationship(
        "Branch", back_populates="repository", lazy="selectin",
        cascade="all, delete-orphan",
    )
    labels = relationship(
        "Label", back_populates="repository", lazy="selectin",
        cascade="all, delete-orphan",
    )
    milestones = relationship(
        "Milestone", back_populates="repository", lazy="selectin",
        cascade="all, delete-orphan",
    )
    issues = relationship(
        "Issue", back_populates="repository", lazy="selectin",
        cascade="all, delete-orphan",
    )


class Collaborator(Base):
    __tablename__ = "collaborators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    permission: Mapped[str] = mapped_column(
        String, default="push"
    )  # "pull", "triage", "push", "maintain", "admin"

    # Relationships
    repository = relationship("Repository", back_populates="collaborators", lazy="selectin")
    user = relationship("User", lazy="selectin")


class StarredRepo(Base):
    __tablename__ = "starred_repos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    # Relationships
    repository = relationship("Repository", back_populates="stars", lazy="selectin")
    user = relationship("User", lazy="selectin")
