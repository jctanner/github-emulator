"""Pull request auto-merge requests."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PullRequestAutoMerge(Base):
    """A persisted request to merge a pull request when it becomes ready."""

    __tablename__ = "pull_request_auto_merges"
    __table_args__ = (
        UniqueConstraint("pull_request_id", name="uq_auto_merge_pull_request"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pull_request_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pull_requests.id"), nullable=False
    )
    enabled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    enabled_by_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    merge_method: Mapped[str] = mapped_column(String, default="MERGE", nullable=False)
    commit_headline: Mapped[str | None] = mapped_column(Text, nullable=True)
    commit_body: Mapped[str | None] = mapped_column(Text, nullable=True)

    pull_request = relationship("PullRequest", back_populates="auto_merge", lazy="selectin")
    enabled_by = relationship("User", lazy="selectin")
