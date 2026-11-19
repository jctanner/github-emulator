"""Give users and organizations one id space.

Users and organizations each had their own autoincrement id, so a user and an
organization could have the same id; on a fresh instance the admin user and
the first organization were both 1. GitHub allocates both from one sequence,
and a repository's ``owner.id`` names one account. A relying party pinning an
owner by id (OIDC ``repository_owner_id``) matched the wrong one here.

New ids come from ``account_ids`` (app/models/account.py). This revision
creates that table, moves every organization whose id a user holds or held
(at or below the highest user id ever handed out) to a fresh id above every
user and organization id handed out so far, rewrites the columns that
reference those organizations, and seeds ``account_ids`` with every id in use
and the highest one ever handed out, so no id is reused across the two tables
(the guarantee 0016 gives within each table).

The referencing columns are the reflected foreign keys to
``organizations.id`` plus the known ones, since 0002 added
``repositories.organization_id`` without a foreign key. Payloads already
stored as JSON (events, webhook deliveries) are snapshots and are left as
they were sent.

Revision ID: 0018_shared_account_ids
Revises: 0017_org_role_assignments
Create Date: 2026-11-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0018_shared_account_ids"
down_revision = "0017_org_role_assignments"
branch_labels = None
depends_on = None

_TABLE = "account_ids"

# Columns holding an organization id, whether or not the database declares
# the foreign key.
_KNOWN_ORG_REFERENCES = (
    ("repositories", "organization_id"),
    ("org_memberships", "org_id"),
    ("org_role_assignments", "org_id"),
    ("teams", "org_id"),
    ("events", "org_id"),
    ("webhooks", "org_id"),
    ("runners", "org_id"),
    ("org_variables", "org_id"),
)


def _highest_id(bind, table: str) -> int:
    """The highest id ``table`` has handed out, deleted rows included."""
    highest = bind.execute(sa.text(f"SELECT max(id) FROM {table}")).scalar() or 0
    if bind.dialect.name == "sqlite":
        has_sequence = bind.execute(
            sa.text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sqlite_sequence'")
        ).scalar()
        if has_sequence:
            seq = bind.execute(
                sa.text("SELECT seq FROM sqlite_sequence WHERE name = :name"), {"name": table}
            ).scalar()
            highest = max(highest, seq or 0)
    return highest


def _org_references(inspector, tables: set[str]) -> set[tuple[str, str]]:
    references = set()
    for table in tables:
        for fk in inspector.get_foreign_keys(table):
            if fk.get("referred_table") == "organizations" and fk.get("referred_columns") == ["id"]:
                references.update((table, column) for column in fk["constrained_columns"])
    for table, column in _KNOWN_ORG_REFERENCES:
        if table in tables and column in {c["name"] for c in inspector.get_columns(table)}:
            references.add((table, column))
    return references


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if _TABLE not in tables:
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sqlite_autoincrement=True,
        )
    if not {"users", "organizations"} <= tables:
        return

    user_high = _highest_id(bind, "users")
    next_id = max(user_high, _highest_id(bind, "organizations"), _highest_id(bind, _TABLE)) + 1
    moved = bind.execute(
        sa.text("SELECT id FROM organizations WHERE id <= :high ORDER BY id"), {"high": user_high}
    ).scalars().all()
    references = sorted(_org_references(inspector, tables))
    # Every new id is above every organization id ever handed out, so it is
    # not the old id of another organization, nor a dangling reference to a
    # deleted one, and the rewrites cannot chain.
    for old_id in moved:
        params = {"old": old_id, "new": next_id}
        bind.execute(sa.text("UPDATE organizations SET id = :new WHERE id = :old"), params)
        for table, column in references:
            bind.execute(sa.text(f"UPDATE {table} SET {column} = :new WHERE {column} = :old"), params)
        next_id += 1

    bind.execute(
        sa.text(
            f"INSERT INTO {_TABLE} (id) SELECT id FROM users "
            f"UNION SELECT id FROM organizations "
            f"EXCEPT SELECT id FROM {_TABLE}"
        )
    )
    # Reserve the highest id handed out so far, which may be a deleted row's,
    # so the sequence continues above it.
    highest = next_id - 1
    if highest > 0 and not bind.execute(
        sa.text(f"SELECT 1 FROM {_TABLE} WHERE id = :id"), {"id": highest}
    ).scalar():
        bind.execute(sa.text(f"INSERT INTO {_TABLE} (id) VALUES (:id)"), {"id": highest})


def downgrade() -> None:
    # Moved organization ids stay moved; they are valid under every earlier
    # revision. Without the table, ids come from each table again.
    if _TABLE in set(inspect(op.get_bind()).get_table_names()):
        op.drop_table(_TABLE)
