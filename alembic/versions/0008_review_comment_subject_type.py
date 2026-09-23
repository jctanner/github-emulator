"""Record whether a review comment is on a line or a whole file.

GitHub's review comments carry subject_type: "line" (the default) or "file".
A file-level comment legitimately has no line and no position, and a client
sends subject_type="file" with line omitted. Without somewhere to keep it, a
file-level comment cannot be told apart from a malformed one, and validation
that demands an anchor rejects exactly the comments that are allowed not to
have one.

Existing rows become "line", which is what they all were.

Revision ID: 0008_review_comment_subject_type
Revises: 0007_repo_workflow_permissions
Create Date: 2026-09-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0008_review_comment_subject_type"
down_revision = "0007_repo_workflow_permissions"
branch_labels = None
depends_on = None


_TABLE = "pr_review_comments"
_COLUMN = "subject_type"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns(_TABLE)}
    if _COLUMN in existing:
        return
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))
    bind.execute(
        sa.text(f"UPDATE {_TABLE} SET {_COLUMN} = :value WHERE {_COLUMN} IS NULL"),
        {"value": "line"},
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns(_TABLE)}
    if _COLUMN in existing:
        op.drop_column(_TABLE, _COLUMN)
