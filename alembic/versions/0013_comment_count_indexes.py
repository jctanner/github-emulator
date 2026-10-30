"""Index source rows used for issue and PR comment counts.

Revision ID: 0013_comment_count_indexes
Revises: 0012_issue_event_source
"""

from alembic import op
import sqlalchemy as sa

revision = "0013_comment_count_indexes"
down_revision = "0012_issue_event_source"
branch_labels = None
depends_on = None

_INDEXES = (
    ("issue_comments", "issue_id", "ix_issue_comments_issue_id"),
    ("pr_review_comments", "pull_request_id", "ix_pr_review_comments_pull_request_id"),
)


def upgrade() -> None:
    for table, column, name in _INDEXES:
        if name not in {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}:
            op.create_index(name, table, [column])


def downgrade() -> None:
    for table, _, name in reversed(_INDEXES):
        if name in {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}:
            op.drop_index(name, table_name=table)
