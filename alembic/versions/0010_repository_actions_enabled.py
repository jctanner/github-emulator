"""Whether Actions runs on a repository.

GitHub's per-repository setting at /repos/{owner}/{repo}/actions/permissions.
NULL means enabled, so existing rows keep their behaviour. A mirror of an
upstream repository carries upstream's CI workflows; disabling Actions on it
is how a mirror stops dispatching them on every push.

Revision ID: 0010_repository_actions_enabled
Revises: 0009_job_workflow_ref
Create Date: 2026-09-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0010_repository_actions_enabled"
down_revision = "0009_job_workflow_ref"
branch_labels = None
depends_on = None

_TABLE = "repositories"
_COLUMN = "actions_enabled"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    if _COLUMN in {column["name"] for column in inspector.get_columns(_TABLE)}:
        return
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.Boolean(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    if _COLUMN not in {column["name"] for column in inspector.get_columns(_TABLE)}:
        return
    op.drop_column(_TABLE, _COLUMN)
