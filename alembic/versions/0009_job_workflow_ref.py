"""Record which workflow defined a job.

A job from an inlined reusable workflow reported the *calling* workflow in
its OIDC token's job_workflow_ref, because the claim was derived from the
run. Upstream keys trust on the called workflow (Fullsend ADR 0082), so a
real mint would refuse the token. The column holds owner/repo/path@ref of
the workflow that defined the job; NULL means the run's own workflow did.

Revision ID: 0009_job_workflow_ref
Revises: 0008_review_comment_subject_type
Create Date: 2026-09-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0009_job_workflow_ref"
down_revision = "0008_review_comment_subject_type"
branch_labels = None
depends_on = None

_TABLE = "workflow_jobs"
_COLUMN = "workflow_ref"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    if _COLUMN in {column["name"] for column in inspector.get_columns(_TABLE)}:
        return
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    if _COLUMN not in {column["name"] for column in inspector.get_columns(_TABLE)}:
        return
    op.drop_column(_TABLE, _COLUMN)
