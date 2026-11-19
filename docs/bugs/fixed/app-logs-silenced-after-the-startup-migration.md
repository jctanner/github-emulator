# Bug: the app's own log lines stop after the startup migration

Status: fixed 2026-11-18.

## Summary

Startup imports `app.main`, which sets up the `github_emulator` logger, and
then runs the Alembic migrations. `alembic/env.py` applied `alembic.ini`'s
logging config with `fileConfig`, whose default disables every logger that
already exists. From then on the `github_emulator` logger dropped every
line: the startup and admin-seeding lines, and the runner broker's
hand-out, acknowledgement and cancellation lines.

The same happened in the test suite. Any test that ran a migration after
another test had imported `app.main` silenced the logger for the rest of
the run, so the startup-logging tests saw an empty capture in CI.

## Fix

- `alembic/env.py` applies `alembic.ini`'s logging only on the command line
  (`configure_logger`, which `app.migrations` sets to False), and never
  disables existing loggers.

Tests: `tests/test_startup_logging.py`.

- The startup migration leaves the app's logger enabled and the root
  logger's handlers unchanged.
- `alembic upgrade` keeps the loggers that exist already.
