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
  the job's end. The message is sent again on every live poll until the
  runner deletes it (its acknowledgement) or reports the job's end, so a
  response the runner abandoned or never got does not lose it. Its message
  id is apart from job message ids, so its acknowledgement is not taken
  for a job's.
- An `Online` poll the runner sent before it took the job, arriving after
  the job was cancelled, does not free the runner within the 2-minute
  grace period, so the cancellation is still sent.
- `_complete_job` keeps a `cancelled` conclusion when the runner reports
  the end, and still frees the runner.

Tests: `tests/actions/test_runner_job_cancellation.py`.

- A cancelled job's runner gets `JobCancellation` with the job's guid, again
  until it acknowledges it.
- `tests/actions/test_runner_broker_races.py`: a cancellation whose response
  was lost is sent again; a late `Online` poll does not drop it.
- A job still running gets none.
- The runner's report keeps `cancelled` and frees the runner.

## Not changed

Acknowledged cancellations are kept in memory, one per runner. After a
restart the runner may get a cancellation once more, and a runner ignores
a cancellation for a job it no longer runs.
