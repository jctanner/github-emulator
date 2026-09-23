"""Give workflow jobs their own concurrency group.

GitHub supports `concurrency:` at job level as well as workflow level, and
Fullsend's dispatch declares a per-role group on every stage job so that a new
event supersedes an in-flight run of the same role without touching the
others. A run-level group cannot express that: it would cancel every stage.

Revision ID: 0006_workflow_job_concurrency
Revises: 0005_import_job_destination
Create Date: 2026-09-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0006_workflow_job_concurrency"
down_revision = "0005_import_job_destination"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "workflow_jobs" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("workflow_jobs")}
    if "concurrency_group" not in existing:
        op.add_column(
            "workflow_jobs", sa.Column("concurrency_group", sa.String(), nullable=True)
        )
        op.create_index(
            "ix_workflow_jobs_concurrency_group", "workflow_jobs", ["concurrency_group"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "workflow_jobs" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("workflow_jobs")}
    if "concurrency_group" in existing:
        op.drop_index("ix_workflow_jobs_concurrency_group", table_name="workflow_jobs")
        op.drop_column("workflow_jobs", "concurrency_group")
