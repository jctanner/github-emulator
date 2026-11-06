"""Review thread resolution and minimized comments.

GraphQL ``resolveReviewThread`` records who resolved a review thread and when,
on the thread's root review comment. ``minimizeComment`` records why an issue
comment, review comment, or review was hidden. Every new column is nullable
and NULL means "unresolved" or "not minimized", so existing rows are
unchanged.

Revision ID: 0014_review_threads_and_minimized_comments
Revises: 0013_comment_count_indexes
Create Date: 2026-11-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0014_review_threads_and_minimized_comments"
down_revision = "0013_comment_count_indexes"
branch_labels = None
depends_on = None

# (table, column, type, foreign key target or None)
_COLUMNS = [
    ("issue_comments", "minimized_reason", sa.String(), None),
    ("pr_review_comments", "minimized_reason", sa.String(), None),
    ("pr_review_comments", "thread_resolved_at", sa.DateTime(), None),
    ("pr_review_comments", "thread_resolved_by_id", sa.Integer(), "users.id"),
    ("reviews", "minimized_reason", sa.String(), None),
]


def _fk_name(table: str, column: str) -> str:
    return f"fk_{table}_{column}"


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table, column, type_, target in _COLUMNS:
        if table not in tables:
            continue
        if column in {item["name"] for item in inspector.get_columns(table)}:
            continue
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column(column, type_, nullable=True))
            if target:
                target_table, target_column = target.split(".")
                batch.create_foreign_key(
                    _fk_name(table, column), target_table, [column], [target_column]
                )


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table, column, _type, target in reversed(_COLUMNS):
        if table not in tables:
            continue
        if column not in {item["name"] for item in inspector.get_columns(table)}:
            continue
        # SQLite does not keep constraint names, so the foreign key is dropped
        # by name only where the database reports that name; batch mode's
        # table rebuild leaves out a foreign key on the dropped column either way.
        named = {fk.get("name") for fk in inspector.get_foreign_keys(table)}
        with op.batch_alter_table(table) as batch:
            if target and _fk_name(table, column) in named:
                batch.drop_constraint(_fk_name(table, column), type_="foreignkey")
            batch.drop_column(column)
