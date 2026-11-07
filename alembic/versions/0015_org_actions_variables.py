"""Organization Actions variables (github-emulator#18).

``org_variables`` holds an organization's variables and their visibility;
``org_variable_repositories`` the repositories selected for a variable whose
visibility is ``selected``. Both are new tables, so existing rows are
untouched.

Revision ID: 0015_org_actions_variables
Revises: 0014_review_threads_and_minimized_comments
Create Date: 2026-11-06
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0015_org_actions_variables"
down_revision = "0014_review_threads_and_minimized_comments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    if "org_variables" not in tables:
        op.create_table(
            "org_variables",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("value", sa.Text(), nullable=False),
            sa.Column("visibility", sa.String(), nullable=False, server_default="all"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
            sa.UniqueConstraint("org_id", "name", name="uq_org_variable_org_name"),
        )
    if "org_variable_repositories" not in tables:
        op.create_table(
            "org_variable_repositories",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("org_variable_id", sa.Integer(), sa.ForeignKey("org_variables.id"), nullable=False),
            sa.Column("repo_id", sa.Integer(), sa.ForeignKey("repositories.id"), nullable=False),
            sa.UniqueConstraint("org_variable_id", "repo_id", name="uq_org_variable_repository"),
        )


def downgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    if "org_variable_repositories" in tables:
        op.drop_table("org_variable_repositories")
    if "org_variables" in tables:
        op.drop_table("org_variables")
