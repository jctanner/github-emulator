from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PullRequest(Base):
    __tablename__ = "pull_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issue_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("issues.id"), unique=True, nullable=False
    )
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id"), nullable=False)

    head_ref: Mapped[str] = mapped_column(String, nullable=False)
    head_sha: Mapped[str] = mapped_column(String, nullable=False)
    head_repo_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("repositories.id"), nullable=True
    )

    base_ref: Mapped[str] = mapped_column(String, nullable=False)
    base_sha: Mapped[str] = mapped_column(String, nullable=False)

    merged: Mapped[bool] = mapped_column(Boolean, default=False)
    mergeable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    merged_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    merge_commit_sha: Mapped[str | None] = mapped_column(String, nullable=True)
    draft: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relationships
    issue = relationship("Issue", back_populates="pull_request", lazy="selectin")
    repository = relationship(
        "Repository", foreign_keys=[repo_id], lazy="selectin"
    )
    head_repository = relationship(
        "Repository", foreign_keys=[head_repo_id], lazy="selectin"
    )
    merged_by = relationship("User", foreign_keys=[merged_by_id], lazy="selectin")
