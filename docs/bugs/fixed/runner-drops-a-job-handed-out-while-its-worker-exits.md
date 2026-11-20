# Bug: a runner drops a job handed out while its previous worker exits

Status: fixed 2026-11-20 (github-emulator#99).

## Summary

A job could fail before any of its steps ran, every step left `queued`. The
runner had reported its previous job's end, but that job's worker had not
exited yet. The broker freed the runner on the report, and the next poll was
handed the new job. Before starting it, the runner's
`JobDispatcher.EnsureDispatchFinished` reads the previous request with
`GET .../pools/{poolId}/jobrequests/{requestId}`. The emulator had no GET
for that path and answered 405. On that error the runner kills the old
worker and does not start the new job, which the broker failed two minutes
later as idle-while-holding.

## Fix

All in `src/app/api/actions_distributed_task.py`:

- `GET .../pools/{poolId}/jobrequests/{requestId}` answers, in the same
  route forms as the POST, PATCH and DELETE (the `/{owner}/{repo}/` form also
  serves `/enterprises/{enterprise}/`). It takes the runner's token and
  returns the `_job_request_response` shape. A completed job adds `result`
  (its conclusion as a camelCase `TaskResult`: `succeeded`, `failed`,
  `canceled`, `skipped`) and `finishTime` (its `completed_at`); a running job
  has neither. Another runner's job, or a missing one, gets the
  `TaskAgentJobNotFoundException` 404. With either answer the runner cancels
  the old worker and runs the new job.
- `_complete_job` leaves a completed row that has a conclusion as it is
  (conclusion, outputs and `completed_at`), not only a cancelled one. A later
  PATCH, complete POST or `JobCompleted` event still frees the runner.
- A poll with `status=Busy` gets a pending cancellation but no new job; it
  long-polls to its end and returns 204. The runner's `Online` poll, sent as
  soon as its worker exits, gets the job. A missing or other status behaves
  as before. GitHub does not document what its broker does here, so this is
  the emulator's choice; the GET alone stops the dropped job.

## Regression coverage

`tests/actions/test_runner_previous_job_request.py`: a job handed out on a
poll with no status, after the previous job's end, runs to completion; a
completed job keeps its first conclusion and outputs through a later PATCH,
`JobCompleted` and complete POST; a `Busy` poll gets no job and the next
`Online` poll does; every route form answers; a running job has no `result`;
each conclusion maps to its `TaskResult` and `finishTime` is `completed_at`;
another runner's job and a missing job are 404 with the `typeKey`; a request
without the runner's token is refused; a `Busy` poll still gets a pending
cancellation.
