"""Actions artifact model.

The row is an index, not a container. Artifact bytes live on disk under
``DATA_DIR/artifacts/<id>/``, the same arrangement job logs already use, so a
large upload does not bloat the SQLite database or have to be base64-encoded to
survive a JSON column.
"""

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
    # Relative path -> size in bytes for each stored file. The contents are on
    # disk; this exists so listing an artifact does not have to walk the
    # filesystem, and so a missing file can be told apart from an empty one.
    files: Mapped[dict] = mapped_column(JSON, default=dict)
    size_in_bytes: Mapped[int] = mapped_column(Integer, default=0)
    expired: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
