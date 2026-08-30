"""Baseline the schema and upgrade pre-Alembic emulator databases.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    inspector = inspect(bind)
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _add_column(bind, table: str, column: sa.Column) -> None:
    if column.name not in _columns(bind, table):
        op.add_column(table, column)


def upgrade() -> None:
    # Importing the model package registers every table on Base.metadata. A
    # check-first create makes this revision both a fresh-schema baseline and a
    # safe entry point for databases created before Alembic was activated.
    from app.database import Base
    import app.models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)

    _add_column(bind, "secrets", sa.Column("value", sa.Text(), nullable=True))
    _add_column(bind, "workflow_jobs", sa.Column("permissions", sa.Text(), nullable=True))
    _add_column(bind, "workflow_runs", sa.Column("concurrency_group", sa.Text(), nullable=True))
    _add_column(bind, "runners", sa.Column("enterprise_slug", sa.String(), nullable=True))
    _add_column(bind, "github_apps", sa.Column("client_id", sa.String(), nullable=True))
    _add_column(bind, "github_apps", sa.Column("bot_user_id", sa.Integer(), nullable=True))

    for name in (
        "required_linear_history",
        "allow_force_pushes",
        "allow_deletions",
        "block_creations",
        "lock_branch",
        "allow_fork_syncing",
    ):
        _add_column(
            bind,
            "branch_protections",
            sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    _add_column(
        bind,
        "pull_requests",
        sa.Column("last_push_by_id", sa.Integer(), nullable=True),
    )

    if "client_id" in _columns(bind, "github_apps"):
        bind.execute(
            sa.text(
                "UPDATE github_apps "
                "SET client_id = 'Iv1.' || lower(hex(randomblob(16))) "
                "WHERE client_id IS NULL OR client_id = ''"
            )
        )
        indexes = {item["name"] for item in inspect(bind).get_indexes("github_apps")}
        if "ix_github_apps_client_id" not in indexes:
            op.create_index(
                "ix_github_apps_client_id",
                "github_apps",
                ["client_id"],
                unique=True,
            )


def downgrade() -> None:
    # This baseline intentionally does not destructively rewrite pre-Alembic
    # SQLite databases. Emulator resets are the supported downgrade mechanism.
    pass
