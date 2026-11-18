# Bug: the upstream runner keeps running a cancelled job

Status: fixed 2026-11-14.

## Summary

On GitHub, cancelling a job that is running stops it on its runner. That
happens when a newer job in the same `concurrency:` group with
`cancel-in-progress: true` supersedes it, or when the run is cancelled
through the API.

The emulator marked the job `completed/cancelled` in the database and never
told the runner. An `actions/runner` runner ran the cancelled job to its
end, so a superseded stage ran twice: once cancelled, once for real. When
the runner then reported the job's end, the report overwrote the
`cancelled` conclusion with the runner's result.

## Fix

- While a runner is busy with a job that has since been cancelled, its
  message long-poll returns one `JobCancellation` message
  (`{"jobId": <job guid>, "timeout": "00:05:00"}`), the shape the runner's
  `JobCancelMessage` reads. The runner cancels the job's steps and reports
  the job's end. The message is sent once per job.
- `_complete_job` keeps a `cancelled` conclusion when the runner reports
  the end, and still frees the runner.

Tests: `tests/actions/test_runner_job_cancellation.py`.

- A cancelled job's runner gets one `JobCancellation` with the job's guid.
- A job still running gets none.
- The runner's report keeps `cancelled` and frees the runner.

## Not changed

The set of jobs a cancellation was sent for is kept in memory. After a
restart the runner may get it once more, and a runner ignores a
cancellation for a job it no longer runs.
