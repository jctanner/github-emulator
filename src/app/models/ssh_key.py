"""SSH and GPG key models for user key management."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SSHKey(Base):
    __tablename__ = "ssh_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False, default="")
    key: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str | None] = mapped_column(String, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, default=True)
    read_only: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    # Relationships
    user = relationship("User", lazy="selectin")


class GPGKey(Base):
    __tablename__ = "gpg_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    key_id: Mapped[str | None] = mapped_column(String, nullable=True)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    emails: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    can_sign: Mapped[bool] = mapped_column(Boolean, default=True)
    can_encrypt_comms: Mapped[bool] = mapped_column(Boolean, default=False)
    can_encrypt_storage: Mapped[bool] = mapped_column(Boolean, default=False)
    can_certify: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", lazy="selectin")
