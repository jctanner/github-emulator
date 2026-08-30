from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # "PushEvent", "CreateEvent", etc.
    actor_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    repo_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("repositories.id"), nullable=True
    )
    org_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=True
    )
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    public: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    # Relationships
    actor = relationship("User", lazy="selectin")
