"""Tell an enterprise remove token from a registration token.

Both live in ``enterprise_runner_registration_tokens``. A registration token
now stays valid until it expires, so a remove token must not register
runners: ``for_remove`` marks the tokens made by the remove-token route.
NULL means a registration token, so existing rows keep their behaviour.

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

_TABLE = "enterprise_runner_registration_tokens"
_COLUMN = "for_remove"


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    if _TABLE not in inspector.get_table_names():
        return
    if _COLUMN in {column["name"] for column in inspector.get_columns(_TABLE)}:
        return
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.Boolean(), nullable=True))


def downgrade() -> None:
    inspector = inspect(op.get_bind())
    if _TABLE not in inspector.get_table_names():
        return
    if _COLUMN not in {column["name"] for column in inspector.get_columns(_TABLE)}:
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column(_COLUMN)
