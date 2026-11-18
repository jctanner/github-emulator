# Bug: a runner stays busy after its job is deleted or lost

Status: fixed 2026-11-14.

## Summary

On GitHub, deleting a repository cancels its running jobs, and the runners
that ran them take new work.

Deleting a repository on the emulator deletes its runs and jobs. An
`actions/runner` runner still running one of those jobs got 401 on every
call and "not found" on its lease. Nothing told it to stop, and the
emulator kept it busy for good, so later jobs queued with no free runner.

The same happened to any runner the emulator held busy with a job the
runner no longer had: the runner polled as `Online`, and the emulator never
offered it another job.

## Fix

- The emulator remembers the job it handed to each runner, apart from the
  job row. When that job no longer exists, the runner's next poll gets
  `JobCancellation` for it.
- A runner that polls with `status=Online` while the emulator holds it busy
  is believed: it is freed, and the job it held fails if that job is still
  in progress. The first two minutes after a hand-out are exempt, because
  the runner can poll once more as `Online` before its dispatcher marks it
  busy.

Tests: `tests/actions/test_runner_orphaned_jobs.py`.

- A deleted job is cancelled on its runner.
- A runner that says `Online` is freed and its lost job fails.
- A runner that says `Busy` is left alone.
- A runner that says `Online` just after taking a job keeps it.

## Known limits

A job stays in progress if the runner's worker process dies without
reporting the job's end (`JobCompleted`, or the job request's finish).
There is no server-side abandonment timeout yet.
