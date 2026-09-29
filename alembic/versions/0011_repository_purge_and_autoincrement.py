"""Purge what deleted repositories left behind; never reuse a repository id.

Deleting a repository used to remove the row and the ORM's cascades (issues,
labels, branches, collaborators, stars) and nothing under Actions, pull
requests, checks or releases; SQLite does not enforce foreign keys here, so
it succeeded. The repositories table had no AUTOINCREMENT, so the next
repository created took the highest deleted id and inherited those rows as
its history: runs from before it existed, another repository's secrets and
variables.

Two changes. Rows owned by a repository that no longer exists are removed,
walking ownership from every ``repo_id`` column down (a job of an orphaned
run goes with it) and clearing dangling pointers (a fork's parent, a pull
request's head repository). Then the repositories table is rebuilt with
AUTOINCREMENT, which seeds sqlite_sequence with the highest id present so no
id is ever handed out twice.

The plan comes from the reflected schema, not the models, so it purges the
tables the database has rather than the ones the code knows today.

Revision ID: 0011_repository_purge_and_autoincrement
Revises: 0010_repository_actions_enabled
Create Date: 2026-09-29
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

from app.services.repository_purge import orphan_purge_statements

revision = "0011_repository_purge_and_autoincrement"
down_revision = "0010_repository_actions_enabled"
branch_labels = None
depends_on = None

_TABLE = "repositories"


def _has_autoincrement(bind) -> bool:
    if bind.dialect.name != "sqlite":
        return True
    ddl = bind.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name"),
        {"name": _TABLE},
    ).scalar_one_or_none()
    return ddl is not None and "AUTOINCREMENT" in ddl.upper()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return

    metadata = sa.MetaData()
    metadata.reflect(bind=bind)
    for statement in orphan_purge_statements(metadata):
        bind.execute(statement)

    if not _has_autoincrement(bind):
        with op.batch_alter_table(
            _TABLE, recreate="always", table_kwargs={"sqlite_autoincrement": True}
        ):
            pass


def downgrade() -> None:
    # The purge is not reversible, and a table that never reuses ids is
    # correct under every earlier revision too.
    return
