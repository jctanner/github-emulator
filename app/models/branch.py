from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Branch(Base):
    __tablename__ = "branches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    sha: Mapped[str] = mapped_column(String, nullable=False)
    protected: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relationships
    repository = relationship("Repository", back_populates="branches", lazy="selectin")
    protection = relationship(
        "BranchProtection", back_populates="branch", uselist=False, lazy="selectin",
        cascade="all, delete-orphan",
    )


class BranchProtection(Base):
    __tablename__ = "branch_protections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    branch_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("branches.id"), nullable=False, unique=True
    )
    required_status_checks: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    enforce_admins: Mapped[bool] = mapped_column(Boolean, default=False)
    required_pull_request_reviews: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    restrictions: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Relationships
    branch = relationship("Branch", back_populates="protection", lazy="selectin")
