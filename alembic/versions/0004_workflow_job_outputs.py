"""Add workflow job outputs, job key, and deferred render state.

Supports the ``needs.<job>.outputs.*`` context: jobs record their YAML key so
dependencies resolve correctly, keep their unrendered ``outputs:`` mapping
until they finish, and hold their condition and steps until dependencies
complete.

Revision ID: 0004_workflow_job_outputs
Revises: 0003_issue_events
Create Date: 2026-09-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0004_workflow_job_outputs"
down_revision = "0003_issue_events"
branch_labels = None
depends_on = None


_COLUMNS = (
    ("job_key", sa.String()),
    ("outputs", sa.JSON()),
    ("outputs_config", sa.JSON()),
    ("pending_render", sa.JSON()),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "workflow_jobs" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("workflow_jobs")}
    for name, column_type in _COLUMNS:
        if name not in existing:
            op.add_column("workflow_jobs", sa.Column(name, column_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "workflow_jobs" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("workflow_jobs")}
    for name, _ in _COLUMNS:
        if name in existing:
            op.drop_column("workflow_jobs", name)
