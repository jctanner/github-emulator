"""Organization roles held by members.

``org_role_assignments`` records which predefined all-repository role
(``app.services.org_roles``) each organization member holds. It is a new
table, so existing rows are untouched. It is AUTOINCREMENT like every other
table since 0016.

Revision ID: 0017_org_role_assignments
Revises: 0016_never_reuse_ids
Create Date: 2026-11-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0017_org_role_assignments"
down_revision = "0016_never_reuse_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    if "org_role_assignments" in tables:
        return
    op.create_table(
        "org_role_assignments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "user_id", "role_id", name="uq_org_role_assignment"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_org_role_assignments_org_id", "org_role_assignments", ["org_id"])
    op.create_index("ix_org_role_assignments_user_id", "org_role_assignments", ["user_id"])


def downgrade() -> None:
    if "org_role_assignments" in set(inspect(op.get_bind()).get_table_names()):
        op.drop_table("org_role_assignments")
