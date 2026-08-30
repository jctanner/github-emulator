from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class IssueComment(Base):
    __tablename__ = "issue_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issue_id: Mapped[int] = mapped_column(Integer, ForeignKey("issues.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user = relationship("User", lazy="selectin")


class PRReviewComment(Base):
    __tablename__ = "pr_review_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pull_request_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pull_requests.id"), nullable=False
    )
    review_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reviews.id"), nullable=True
    )
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    side: Mapped[str | None] = mapped_column(String, nullable=True)
    commit_id: Mapped[str] = mapped_column(String, nullable=False)
    original_commit_id: Mapped[str | None] = mapped_column(String, nullable=True)
    diff_hunk: Mapped[str | None] = mapped_column(Text, nullable=True)
    in_reply_to_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user = relationship("User", lazy="selectin")


class CommitComment(Base):
    __tablename__ = "commit_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str | None] = mapped_column(String, nullable=True)
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user = relationship("User", lazy="selectin")
