"""Development GitHub App and installation records.

The emulator keeps the private key and installation tokens in its local
database because this is a resettable test service.  These fields must not be
copied to a production deployment.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class GitHubApp(Base):
    __tablename__ = "github_apps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    private_key_pem: Mapped[str] = mapped_column(Text, nullable=False)
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)
    # GitHub App API calls are performed as the App's bot account.  Keep the
    # installation owner separate so account and repository semantics remain
    # intact.
    bot_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    installations = relationship("AppInstallation", back_populates="app", lazy="selectin")
    bot_user = relationship("User", foreign_keys=[bot_user_id], lazy="selectin")

class AppInstallation(Base):
    __tablename__ = "app_installations"
    __table_args__ = (UniqueConstraint("app_id", "account_login", name="uq_app_account"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[int] = mapped_column(Integer, ForeignKey("github_apps.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    account_login: Mapped[str] = mapped_column(String, nullable=False, index=True)
    account_type: Mapped[str] = mapped_column(String, default="Organization")
    repositories: Mapped[list] = mapped_column(JSON, default=list)
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    app = relationship("GitHubApp", back_populates="installations", lazy="selectin")
    user = relationship("User", lazy="selectin")
    tokens = relationship("AppInstallationToken", back_populates="installation", lazy="selectin")


class AppInstallationToken(Base):
    __tablename__ = "app_installation_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    installation_id: Mapped[int] = mapped_column(Integer, ForeignKey("app_installations.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    token_prefix: Mapped[str] = mapped_column(String(8), nullable=False)
    repositories: Mapped[list] = mapped_column(JSON, default=list)
    permissions: Mapped[dict] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    installation = relationship("AppInstallation", back_populates="tokens", lazy="selectin")
