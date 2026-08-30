from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Gist(Base):
    __tablename__ = "gists"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # UUID
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    public: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    user = relationship("User", lazy="selectin")
    files = relationship("GistFile", back_populates="gist", lazy="selectin")


class GistFile(Base):
    __tablename__ = "gist_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gist_id: Mapped[str] = mapped_column(String, ForeignKey("gists.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String, nullable=True)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_url: Mapped[str | None] = mapped_column(String, nullable=True)

    # Relationships
    gist = relationship("Gist", back_populates="files", lazy="selectin")
