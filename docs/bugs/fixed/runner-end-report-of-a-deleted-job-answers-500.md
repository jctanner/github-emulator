# Bug: a job end report for a deleted repository's job answers 500

Status: fixed 2026-11-20 (github-emulator#101).

## Summary

A runner's report of a job's end could answer 500 with
`sqlalchemy.exc.InvalidRequestError: Could not refresh instance
'<WorkflowJob ...>'`. The report passed `_report_job_end`'s ownership check.
Then the repository was deleted (`DELETE /repos/{owner}/{repo}`), which
deletes its runs and jobs. `_complete_job`'s conditional `UPDATE` matched no
row, and the `db.refresh(job)` after it raised. The job had already
finished, so no workflow result was lost. The runner just got a 500 for a
duplicate end report of a job that no longer existed.

## Fix

In `_complete_job` (`src/app/api/actions_distributed_task.py`), when the
conditional `UPDATE` matches no row, the job's row is looked up. If it is
gone, the job is not refreshed. The runner is still freed unless it has
already taken another job. Dispatch and run completion are skipped, since
there is no run left. Callers then answer as they do for any other
already-finished job (200). A job that still exists and is already complete
keeps its first end, as before.

## Regression coverage

`tests/actions/test_runner_previous_job_request.py`: a job deleted after a
report's ownership check gets a 200, not a 500, for a `JobCompleted` event, a
second `PATCH .../jobrequests/{requestId}` and a second
`POST .../jobs/{id}/complete`, and its runner is left idle. The existing
first-end-report-kept test still passes.
