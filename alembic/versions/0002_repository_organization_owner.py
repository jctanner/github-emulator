"""Represent an organization repository's namespace owner explicitly.

Revision ID: 0002_repo_org_owner
Revises: 0001_baseline
Create Date: 2026-08-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0002_repo_org_owner"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in inspect(bind).get_columns("repositories")}
    if "organization_id" not in columns:
        op.add_column(
            "repositories",
            sa.Column("organization_id", sa.Integer(), nullable=True),
        )

    # Older rows encoded the namespace only in full_name and kept owner_id as
    # the human creator. Recover the organization identity without resetting data.
    bind.execute(
        sa.text(
            "UPDATE repositories "
            "SET organization_id = ("
            "  SELECT organizations.id FROM organizations "
            "  WHERE organizations.login = substr("
            "    repositories.full_name, 1, instr(repositories.full_name, '/') - 1"
            "  )"
            ") "
            "WHERE owner_type = 'Organization' AND organization_id IS NULL"
        )
    )


def downgrade() -> None:
    # Emulator resets are the supported destructive schema rollback mechanism.
    pass
