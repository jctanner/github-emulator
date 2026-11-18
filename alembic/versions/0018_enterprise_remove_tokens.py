"""Keep config.sh remove's tokens and credentials to removing a runner.

- ``enterprise_runner_registration_tokens.for_remove``: both kinds of token
  live in that table, and a registration token now stays valid until it
  expires, so a remove token must not register runners. NULL is a
  registration token.
- ``runners.remove_only``: the credential a remove exchange returns may only
  delete an agent. NULL is an ordinary runner.

Existing rows keep their behaviour.

Revision ID: 0018_enterprise_remove_tokens
Revises: 0017_org_role_assignments
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0018_enterprise_remove_tokens"
down_revision = "0017_org_role_assignments"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("enterprise_runner_registration_tokens", "for_remove"),
    ("runners", "remove_only"),
)


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table, column in _COLUMNS:
        if table not in tables:
            continue
        if column in {c["name"] for c in inspector.get_columns(table)}:
            continue
        op.add_column(table, sa.Column(column, sa.Boolean(), nullable=True))


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table, column in _COLUMNS:
        if table not in tables:
            continue
        if column not in {c["name"] for c in inspector.get_columns(table)}:
            continue
        with op.batch_alter_table(table) as batch:
            batch.drop_column(column)
