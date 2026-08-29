from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, Boolean
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Runner(Base):
    __tablename__ = "runners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    os: Mapped[str] = mapped_column(String, default="linux")
    status: Mapped[str] = mapped_column(String, default="offline")
    labels: Mapped[list] = mapped_column(JSON, default=list)
    busy: Mapped[bool] = mapped_column(Boolean, default=False)
    token_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    repo_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("repositories.id"), nullable=True)
    org_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("organizations.id"), nullable=True)
    enterprise_slug: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    sessions = relationship("RunnerSession", back_populates="runner", lazy="selectin")


class RunnerSession(Base):
    __tablename__ = "runner_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    runner_id: Mapped[int] = mapped_column(Integer, ForeignKey("runners.id"), nullable=False)
    session_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    runner = relationship("Runner", back_populates="sessions", lazy="selectin")


class RegistrationToken(Base):
    __tablename__ = "registration_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class EnterpriseRunnerRegistrationToken(Base):
    __tablename__ = "enterprise_runner_registration_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    enterprise_slug: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, default="active")

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    runs = relationship("WorkflowRun", back_populates="workflow", lazy="selectin")


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workflow_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workflows.id"), nullable=False
    )
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id"), nullable=False)
    head_sha: Mapped[str] = mapped_column(String, nullable=False)
    head_branch: Mapped[str] = mapped_column(String, nullable=False)
    event: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, default="queued")
    conclusion: Mapped[str | None] = mapped_column(String, nullable=True)
    run_number: Mapped[int] = mapped_column(Integer, nullable=False)
    run_attempt: Mapped[int] = mapped_column(Integer, default=1)
    actor_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    trigger_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    concurrency_group: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    workflow = relationship("Workflow", back_populates="runs", lazy="selectin")
    actor = relationship("User", lazy="selectin")
    jobs = relationship("WorkflowJob", back_populates="run", lazy="selectin")


class WorkflowJob(Base):
    __tablename__ = "workflow_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workflow_runs.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    workflow_name: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="queued")
    conclusion: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    runner_name: Mapped[str | None] = mapped_column(String, nullable=True)
    runner_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("runners.id"), nullable=True)
    labels: Mapped[list | None] = mapped_column(JSON, nullable=True)
    run_attempt: Mapped[int] = mapped_column(Integer, default=1)
    needs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    permissions: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    # Relationships
    run = relationship("WorkflowRun", back_populates="jobs", lazy="selectin")


class Secret(Base):
    __tablename__ = "secrets"
    __table_args__ = (
        UniqueConstraint("repo_id", "name", name="uq_secret_repo_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # Development-emulator value. Production GitHub stores an encrypted value;
    # this local implementation accepts plaintext only through its explicitly
    # local seed path and never returns it from the API.
    value: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Variable(Base):
    __tablename__ = "variables"
    __table_args__ = (
        UniqueConstraint("repo_id", "name", name="uq_variable_repo_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[int] = mapped_column(Integer, ForeignKey("repositories.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
