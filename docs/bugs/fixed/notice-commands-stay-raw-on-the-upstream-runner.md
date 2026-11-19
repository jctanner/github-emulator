# Bug: notice commands stay raw on the upstream runner

Status: fixed 2026-11-15.

## Summary

On the real actions/runner, `echo "::notice::text"` stayed in the job log
as `::notice::text`. `::warning::` and `::error::` rendered as
`##[warning]` and `##[error]`, as on GitHub.

The runner processes `notice` only when the job message sets
`DistributedTask.EnhancedAnnotations` (ActionCommandManager: "Enhanced
Annotations not enabled on the server: 'notice' command will not be
processed"). GitHub sends it; the emulator did not.

## Fix

The job request message sets `DistributedTask.EnhancedAnnotations` to
`true`. The emulator's Python runner was already right
(`workflow-commands-are-not-rendered-in-job-logs.md`).

Tests: `tests/actions/test_execution.py`
(`test_pool_distributed_task_protocol_flow`).
