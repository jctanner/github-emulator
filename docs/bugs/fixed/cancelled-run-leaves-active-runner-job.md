# Cancelled workflow leaves an active runner job

## Symptom

Canceling a workflow run changed the run to `completed/cancelled`, but jobs
that had already been claimed by a runner remained `in_progress`. A restarted
runner could continue servicing the orphaned job, while newer jobs stayed
queued indefinitely.

## Cause

`cancel_workflow_run()` only finalized `queued` and `waiting` jobs. The runner
poll endpoint also selected queued jobs without checking the parent run state,
so cancellation did not form a complete boundary for runner work.

## Fix

Cancellation now finalizes every unfinished job as `completed/cancelled`, and
runner polling only claims queued jobs whose parent run is still `queued` or
`in_progress`.

## Regression coverage

`tests/actions/test_fidelity.py` covers cancellation of an already active job.
