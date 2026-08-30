from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Release(Base):
    __tablename__ = "releases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id"), nullable=False)
    tag_name: Mapped[str] = mapped_column(String, nullable=False)
    target_commitish: Mapped[str] = mapped_column(String, default="main")
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft: Mapped[bool] = mapped_column(Boolean, default=False)
    prerelease: Mapped[bool] = mapped_column(Boolean, default=False)
    author_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    author = relationship("User", lazy="selectin")
    assets = relationship("ReleaseAsset", back_populates="release", lazy="selectin")


class ReleaseAsset(Base):
    __tablename__ = "release_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    release_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("releases.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    download_count: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String, default="uploaded")
    uploader_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    browser_download_url: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    release = relationship("Release", back_populates="assets", lazy="selectin")
    uploader = relationship("User", lazy="selectin")
