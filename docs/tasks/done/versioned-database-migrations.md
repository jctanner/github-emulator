# Task: Versioned Database Migrations

## Goal

Replace startup-time compatibility DDL with Alembic revisions and prove that an
older emulator database upgrades without losing data.

## Acceptance Criteria

- [x] Alembic is an installed runtime dependency.
- [x] Existing compatibility columns and indexes are represented by revisions.
- [x] Startup upgrades the configured database before normal use.
- [x] A representative old SQLite schema upgrades with existing rows intact.
- [x] Fresh-database and full regression tests pass.

## Status

Complete

## Validation

- Fresh and representative legacy-schema migration tests: 2 passed.
- Complete suite: 368 passed.
- Live persisted database upgraded and reported revision `0001_baseline`.
