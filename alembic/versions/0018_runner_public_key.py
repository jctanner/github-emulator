"""Keep the RSA public key an upstream runner registers.

actions/runner proves itself on the OAuth token exchange with a JWT client
assertion signed by the RSA key it sent at registration. The emulator never
stored that key, so it could not check the assertion and issued a runner
token to anyone who knew a runner id. NULL means no key was registered;
such a runner cannot use the token exchange until it registers again.

Revision ID: 0018_runner_public_key
Revises: 0017_org_role_assignments
Create Date: 2026-11-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0018_runner_public_key"
down_revision = "0017_org_role_assignments"
branch_labels = None
depends_on = None

_TABLE = "runners"
_COLUMN = "public_key"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    if _COLUMN in {column["name"] for column in inspector.get_columns(_TABLE)}:
        return
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    if _COLUMN not in {column["name"] for column in inspector.get_columns(_TABLE)}:
        return
    op.drop_column(_TABLE, _COLUMN)
