# Bug: a cancellation never reaches a runner that moved on

Status: fixed 2026-11-14.

## Summary

With four `actions/runner` runners, a superseded stage ran to its end
beside the job that superseded it. The job was cancelled in the database,
but its runner never got `JobCancellation`. Two causes:

- **A late report freed the runner.** The runner finished job A and took
  job B in the same second. A's finish report landed after B's claim and
  marked the runner idle. The cancellation check sends only to a busy
  runner, so it never fired for B, and the emulator could offer the runner
  a second job while it ran B.
- **The long poll read a stale job.** The message poll keeps one database
  session for its 30 s. That session does not expire objects on commit,
  and under SQLite WAL its read transaction pins the snapshot it started
  with. A cancellation committed during the poll stayed invisible to it.

## Fix

- A job report frees the runner only if the runner has not moved on to
  another job (held in memory, or in progress on it in the database).
- Each pass of the long poll ends its transaction, and the cancellation
  check reads the job fresh.

Tests: `tests/actions/test_runner_cancel_delivery.py`.

- A cancellation committed in another session is seen by a session that
  had already loaded the job.
- A late report of the previous job leaves the runner busy with its new
  job.
- The report of the job the runner holds still frees it.
