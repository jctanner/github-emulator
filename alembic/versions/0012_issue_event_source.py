"""The issue a cross-referenced event points back at.

GitHub's cross-referenced timeline event names the issue or pull request whose
body or comment mentioned the issue. NULL for every other event, so existing
rows are unchanged.

Revision ID: 0012_issue_event_source
Revises: 0011_repository_purge_and_autoincrement
Create Date: 2026-10-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0012_issue_event_source"
down_revision = "0011_repository_purge_and_autoincrement"
branch_labels = None
depends_on = None

_TABLE = "issue_events"
_COLUMN = "source_issue_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    if _COLUMN in {column["name"] for column in inspector.get_columns(_TABLE)}:
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column(_COLUMN, sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_issue_events_source_issue_id", "issues", [_COLUMN], ["id"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    if _COLUMN not in {column["name"] for column in inspector.get_columns(_TABLE)}:
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint("fk_issue_events_source_issue_id", type_="foreignkey")
        batch.drop_column(_COLUMN)
