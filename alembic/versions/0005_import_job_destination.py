"""Record the destination owner type and organization login on import jobs.

Lets a single-repo import target an Organization namespace, and lets the
admin imports list display the real destination instead of always showing
the acting admin's own login.

Revision ID: 0005_import_job_destination
Revises: 0004_workflow_job_outputs
Create Date: 2026-09-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0005_import_job_destination"
down_revision = "0004_workflow_job_outputs"
branch_labels = None
depends_on = None


_COLUMNS = (
    ("owner_type", sa.String()),
    ("org_login", sa.String()),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "import_jobs" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("import_jobs")}
    for name, column_type in _COLUMNS:
        if name not in existing:
            op.add_column("import_jobs", sa.Column(name, column_type, nullable=True))
    if "owner_type" not in existing:
        bind.execute(sa.text("UPDATE import_jobs SET owner_type = 'User' WHERE owner_type IS NULL"))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "import_jobs" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("import_jobs")}
    for name, _ in _COLUMNS:
        if name in existing:
            op.drop_column("import_jobs", name)
