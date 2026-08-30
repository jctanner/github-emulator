from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id"), nullable=False)
    subject_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # "Issue", "PullRequest", "Release", "Commit"
    subject_title: Mapped[str] = mapped_column(String, nullable=False)
    subject_url: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(
        String, nullable=False
    )  # "subscribed", "manual", "author", "comment", "mention", "team_mention", "state_change", "assign"
    unread: Mapped[bool] = mapped_column(Boolean, default=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", lazy="selectin")
