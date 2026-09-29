"""Delete everything a repository owns, in foreign-key order.

GitHub deletes a repository and everything under it: its Actions history,
secrets, variables, pull requests, checks, releases. This emulator used to
delete the repository row and whatever the ORM cascaded (issues, labels,
branches, collaborators, stars) and leave the rest behind, and SQLite does
not enforce foreign keys here, so the delete succeeded. Combined with the
repositories table reusing ids, the next repository created inherited the
deleted one's runs, workflows, secrets and variables as its own history.

The plan is derived from the table metadata rather than written by hand, so
a new table that hangs off a repository is purged the day it is added:

- a column named ``repo_id`` that references ``repositories.id`` owns its
  rows, so they are deleted, after anything that references them in turn;
- any other column referencing ``repositories.id`` (a fork's ``parent_id``,
  a pull request's ``head_repo_id``) is a pointer, not ownership, and is set
  to NULL, which is what GitHub shows for a fork whose parent is gone;
- below the repository, a row belongs to what it must name (a job to its
  run) and merely points at what it may leave empty (a job to its runner),
  so the first is deleted with its parent and the second is cleared.

The same plan runs in migration 0011 against the reflected schema to purge
rows that earlier deletes left behind, so it takes a :class:`MetaData`
rather than importing the models.
"""

from __future__ import annotations

from sqlalchemy import Column, MetaData, Table, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ClauseElement

OWNER_COLUMN = "repo_id"
REPOSITORIES = "repositories"


def _references(metadata: MetaData, parent: Table) -> list[tuple[Table, Column]]:
    """Every (table, column) whose column references *parent*'s primary key."""
    found = []
    for table in metadata.sorted_tables:
        for column in table.columns:
            if any(fk.column.table is parent for fk in column.foreign_keys):
                found.append((table, column))
    return found


def _descend(
    metadata: MetaData,
    parent: Table,
    parent_ids: ClauseElement,
    statements: list[ClauseElement],
    seen: set[tuple[str, str]],
) -> None:
    """Append the statements that remove what the rows of *parent* whose ids
    are in *parent_ids* own, deepest first."""
    repositories = metadata.tables[REPOSITORIES]
    for table, column in _references(metadata, parent):
        key = (table.name, column.name)
        if key in seen:
            continue
        seen.add(key)
        if parent is repositories:
            owned = column.name == OWNER_COLUMN
        else:
            # Below the repository, a row belongs to what it must name (a
            # job to its run, a review to its pull request); a reference it
            # may leave empty (a job's runner, an issue's milestone) is a
            # pointer, and deleting the target must not take the row.
            owned = not column.nullable
        if table is parent or not owned:
            # A pointer at the deleted rows, not a row they own.
            statements.append(
                update(table).where(column.in_(parent_ids)).values({column.name: None})
            )
            continue
        _descend(metadata, table, select(table.c.id).where(column.in_(parent_ids)), statements, seen)
        statements.append(delete(table).where(column.in_(parent_ids)))


def purge_statements(metadata: MetaData, repository_ids: ClauseElement) -> list[ClauseElement]:
    """The statements that remove everything the repositories in
    *repository_ids* (a selectable of ids) own, children first, ending with
    the repository rows themselves."""
    repositories = metadata.tables[REPOSITORIES]
    statements: list[ClauseElement] = []
    _descend(metadata, repositories, repository_ids, statements, set())
    statements.append(delete(repositories).where(repositories.c.id.in_(repository_ids)))
    return statements


async def purge_repository(db: AsyncSession, metadata: MetaData, repository_id: int) -> None:
    """Delete one repository and everything it owns. The caller commits."""
    repositories = metadata.tables[REPOSITORIES]
    ids = select(repositories.c.id).where(repositories.c.id == repository_id)
    for statement in purge_statements(metadata, ids):
        await db.execute(statement)


def orphan_purge_statements(metadata: MetaData) -> list[ClauseElement]:
    """Statements that remove rows owned by a repository that no longer
    exists. Ownership is followed from the ``repo_id`` columns down, so a job
    of an orphaned run goes with it; a dangling pointer (a fork's parent, a
    pull request's head repository) is cleared. The orphans' ids are by
    definition in no table, so each owner table is walked from the predicate
    "names no repository" rather than from a list of ids."""
    repositories = metadata.tables[REPOSITORIES]
    live = select(repositories.c.id)
    statements: list[ClauseElement] = []
    seen: set[tuple[str, str]] = set()
    for table, column in _references(metadata, repositories):
        seen.add((table.name, column.name))
        dangling = (column.isnot(None), column.notin_(live))
        if table is repositories or column.name != OWNER_COLUMN:
            statements.append(update(table).where(*dangling).values({column.name: None}))
            continue
        _descend(metadata, table, select(table.c.id).where(*dangling), statements, seen)
        statements.append(delete(table).where(*dangling))
    return statements
