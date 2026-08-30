from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String, nullable=False)  # "single" or "bulk"
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    repo_name: Mapped[str | None] = mapped_column(String, nullable=True)
    owner_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    parent_job_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("import_jobs.id"), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    repo_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    owner = relationship("User", lazy="selectin")
    parent_job = relationship(
        "ImportJob", remote_side="ImportJob.id", lazy="selectin"
    )
    child_jobs = relationship(
        "ImportJob", back_populates="parent_job", lazy="selectin"
    )
