"""Search index models for code and commit search."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FileContent(Base):
    """Indexed file content for code search."""
    __tablename__ = "file_contents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id"), nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    blob_sha: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)  # null for binary
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    ref: Mapped[str] = mapped_column(String, nullable=False, default="main")

    indexed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class CommitMetadata(Base):
    """Indexed commit metadata for commit search."""
    __tablename__ = "commit_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id"), nullable=False)
    commit_sha: Mapped[str] = mapped_column(String, nullable=False)
    author_name: Mapped[str | None] = mapped_column(String, nullable=True)
    author_email: Mapped[str | None] = mapped_column(String, nullable=True)
    committer_name: Mapped[str | None] = mapped_column(String, nullable=True)
    committer_email: Mapped[str | None] = mapped_column(String, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_date: Mapped[str | None] = mapped_column(String, nullable=True)
    committer_date: Mapped[str | None] = mapped_column(String, nullable=True)

    indexed_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
