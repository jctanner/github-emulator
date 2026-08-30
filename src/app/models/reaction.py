from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Reaction(Base):
    __tablename__ = "reactions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "content", "reactable_type", "reactable_id",
            name="uq_reaction_user_content_target",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    content: Mapped[str] = mapped_column(
        String, nullable=False
    )  # "+1", "-1", "laugh", "confused", "heart", "hooray", "rocket", "eyes"
    reactable_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # "issue", "issue_comment", "pr_review_comment", "commit_comment"
    reactable_id: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    # Relationships
    user = relationship("User", lazy="selectin")
