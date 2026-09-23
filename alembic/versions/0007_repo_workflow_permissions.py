"""Store the repository default workflow permissions.

On GitHub a job that declares no `permissions:` block inherits the
repository's default, set through
GET/PUT /repos/{owner}/{repo}/actions/permissions/workflow. The emulator had
nowhere to keep that value, so such a job was treated as permissive — which
wrongly allows writes on a repository set to the restricted default.

Defaults to "write" so existing repositories keep the behaviour they had.

Revision ID: 0007_repo_workflow_permissions
Revises: 0006_workflow_job_concurrency
Create Date: 2026-09-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0007_repo_workflow_permissions"
down_revision = "0006_workflow_job_concurrency"
branch_labels = None
depends_on = None


_COLUMNS = (
    ("default_workflow_permissions", sa.String(), "write"),
    ("can_approve_pull_request_reviews", sa.Boolean(), False),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "repositories" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("repositories")}
    for name, column_type, default in _COLUMNS:
        if name in existing:
            continue
        op.add_column("repositories", sa.Column(name, column_type, nullable=True))
        bind.execute(
            sa.text(f"UPDATE repositories SET {name} = :value WHERE {name} IS NULL"),
            {"value": default},
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "repositories" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("repositories")}
    for name, _type, _default in _COLUMNS:
        if name in existing:
            op.drop_column("repositories", name)
