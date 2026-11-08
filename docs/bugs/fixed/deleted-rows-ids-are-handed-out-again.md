# Bug: the id of a deleted row is handed out again

Status: fixed 2026-11-07 (migration 0016, `app/models/__init__.py`).

## Summary

GitHub never reuses an id. The emulator did, in every table but
`repositories`: deleting the newest row of a table freed its id for the next
insert. Fullsend's behaviour suite deletes each slot repository when it gives
the slot back. The next repository created in that slot then got the deleted
one's workflow run and job ids: two consecutive runs of the triage scenario
both ran as workflow run 1962 with jobs 8510 onwards, although the runs were
different (only one installed a vendored binary).

## Cause

The models declare `primary_key=True, autoincrement=True`. On SQLite,
SQLAlchemy does not emit the `AUTOINCREMENT` keyword for that; a table needs
`sqlite_autoincrement=True`. Without the keyword, SQLite gives an `INTEGER
PRIMARY KEY` the value `max(rowid) + 1`. Migration 0011 fixed this for
`repositories` alone, after a recreated repository inherited a deleted one's
history. See `deleted-repository-leaves-its-history-for-the-next-id.md`.
Runs, jobs, issues, pull requests, artifacts and every other table were left
as they were.

## Fix

- **`app/models/__init__.py`.** Once every model is imported, every table is
  marked `sqlite_autoincrement`, so a fresh database and any model added
  later are covered. SQLite applies the keyword only to a single `INTEGER`
  primary key; `gists`, keyed by a string, is unaffected.
- **Migration `0016_never_reuse_ids`.** It rebuilds each table of an
  existing database that has a single `INTEGER` primary key and lacks the
  keyword, as 0011 rebuilt `repositories`. Rows keep their ids, and
  `sqlite_sequence` starts at the highest id present. An id deleted before
  the upgrade, above the highest one left, cannot be recovered and is used
  once more.

## Evidence

- `tests/test_database_migrations.py`:
  - `test_fresh_database_never_reuses_an_id`;
  - `test_existing_tables_are_rebuilt_to_stop_reusing_ids`.

  Both fail with the migration and the model change removed.
- A copy of the live Breadboard database (100 MB, at 0015) upgrades in
  2.6 s. Reflected before and after, every table keeps its columns, types,
  defaults, indexes, unique, foreign-key and check constraints, and row
  counts. Every table except `gists` is `AUTOINCREMENT`, no sequence is
  below its table's highest id, and `PRAGMA integrity_check` is `ok`.
