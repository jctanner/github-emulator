"""Never reuse an id, in any table.

0011 rebuilt the repositories table with AUTOINCREMENT, so a deleted
repository's id is never handed out again. Every other table still reuses
ids: SQLite gives an INTEGER PRIMARY KEY without AUTOINCREMENT the value
max(rowid) + 1, so deleting the newest rows frees their ids. Deleting a
repository deletes its workflow runs and jobs, and the next repository's
first run took the deleted run's id and its jobs the deleted jobs' ids. On
GitHub an id names one object for good; a client that holds an id across a
delete, or caches by it, attaches to a different object here.

The models now declare every table AUTOINCREMENT (app/models/__init__.py),
which covers a fresh database. This revision rebuilds each table of an
existing one that has a single INTEGER primary key and lacks the keyword,
the way 0011 rebuilt repositories. Rebuilding copies the rows with their ids
and seeds sqlite_sequence with the highest id present, so no id in use is
handed out again. An id deleted before this revision, above the highest one
left, cannot be recovered and may be used once more.

The plan comes from the reflected schema, not the models, so it rebuilds the
tables the database has.

Revision ID: 0016_never_reuse_ids
Revises: 0015_org_actions_variables
Create Date: 2026-11-07
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0016_never_reuse_ids"
down_revision = "0015_org_actions_variables"
branch_labels = None
depends_on = None


def _tables_to_rebuild(bind) -> list[str]:
    inspector = inspect(bind)
    tables = []
    for name in inspector.get_table_names():
        if name == "alembic_version":
            continue
        primary_key = inspector.get_pk_constraint(name).get("constrained_columns") or []
        if len(primary_key) != 1:
            continue
        column = next(c for c in inspector.get_columns(name) if c["name"] == primary_key[0])
        if not isinstance(column["type"], sa.Integer):
            continue
        ddl = bind.execute(
            sa.text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name"),
            {"name": name},
        ).scalar_one_or_none()
        if ddl is not None and "AUTOINCREMENT" not in ddl.upper():
            tables.append(name)
    return tables


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    for name in _tables_to_rebuild(bind):
        with op.batch_alter_table(
            name, recreate="always", table_kwargs={"sqlite_autoincrement": True}
        ):
            pass


def downgrade() -> None:
    # A table that never reuses ids is correct under every earlier revision.
    return
