"""Mark the short-lived broker identity config.sh registers.

actions/runner first registers a broker identity with the registration token,
then uses it to register the agent or, with --replace, to update an existing
agent. Only that identity may replace another runner's key, so it is recorded
explicitly instead of being inferred from the runner's repository or
enterprise. Existing rows are not brokers.

Revision ID: 0021_runner_registration_broker
Revises: 0019_runner_public_key
Create Date: 2026-11-19
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0021_runner_registration_broker"
down_revision = "0020_runner_public_key"
branch_labels = None
depends_on = None

_TABLE = "runners"
_COLUMN = "registration_broker"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    if _COLUMN in {column["name"] for column in inspector.get_columns(_TABLE)}:
        return
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    if _COLUMN not in {column["name"] for column in inspector.get_columns(_TABLE)}:
        return
    op.drop_column(_TABLE, _COLUMN)
