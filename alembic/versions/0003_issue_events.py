"""Persist issue conversation events.

Revision ID: 0003_issue_events
Revises: 0002_repo_org_owner
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0003_issue_events"
down_revision = "0002_repo_org_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "issue_events" in inspector.get_table_names():
        return
    op.create_table(
        "issue_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("issue_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("label", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["issue_id"], ["issues.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_issue_events_issue_id", "issue_events", ["issue_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if "issue_events" in inspect(bind).get_table_names():
        op.drop_index("ix_issue_events_issue_id", table_name="issue_events")
        op.drop_table("issue_events")
