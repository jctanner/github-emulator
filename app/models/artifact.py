"""Small JSON-backed Actions artifact model for emulator tests."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WorkflowArtifact(Base):
    __tablename__ = "workflow_artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("workflow_runs.id"), nullable=False, index=True)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    files: Mapped[dict] = mapped_column(JSON, default=dict)
    size_in_bytes: Mapped[int] = mapped_column(Integer, default=0)
    expired: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
