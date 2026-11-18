# Bug: a runner deleting itself leaves its jobs pointing at it

Status: fixed 2026-11-15.

## Summary

Two runner bugs that a PostgreSQL run surfaced. Both also hold on SQLite.

- **Dangling `runner_id`.** A runner deleting itself through the runner
  protocol (`DELETE .../pools/{id}/agents/{id}`) or the pipelines routes
  left the jobs it ran pointing at the deleted row. SQLite does not
  enforce the foreign key; PostgreSQL refused the delete with a 500. The
  admin route already detached the history.
- **Stale pool poll.** The pool long-poll ran every pass in one
  transaction. A database that keeps one read snapshot per transaction
  did not see a job queued during the poll.

## Fix

- Every runner delete uses the admin route's helper,
  `_delete_runner_and_detach_history`. It detaches the jobs (their
  `runner_name` stays) and removes the runner's sessions.
- The pool long-poll commits before each wait, as the distributedtask poll
  does.

Tests: `tests/actions/test_execution.py::test_pool_distributed_task_protocol_flow`
now checks that no job points at the runner after the agent delete. It
fails on the old code.
