# Bug: job outputs are empty on the upstream runner

Status: fixed 2026-11-18.

## Summary

On the upstream actions/runner, every `needs.<job>.outputs.<name>` rendered
empty, so a job could not pass a value to the jobs that need it. The
emulator's Python runner was not affected.

The upstream runner evaluates a job's `outputs:` itself and reports them
only in the `JobCompleted` plan event. Three things stopped that:

- The job message carried no `jobOutputs` template, so the runner had
  nothing to evaluate.
- The job message declared plan version 1. The worker raises
  `JobCompleted` only from plan version 8 (`PlanUtil.GetFeatures`).
- The emulator neither advertised nor served the plan events resource.

## Fix

- The job message sends the declared outputs as a `jobOutputs` template
  mapping, with `${{ }}` parts as expressions.
- The job message declares plan version 8.
- The plan events resource is advertised and served. `JobCompleted`,
  accepted from the job's own token, stores the job's non-secret outputs
  and then completes the job. At version 8 the listener no longer finishes
  the job request, so this is what releases the jobs that need it, with the
  outputs in place.
- The event needs the job's own token with a valid signature, and only a
  job in progress takes it.
- The job token now lasts as long as the job, as GitHub's does: its
  timeout (default 360 minutes) plus 5 minutes, at most 24 hours. It
  lasted one hour, so a longer job could not report its completion.

Tests: `tests/actions/test_runner_job_outputs.py`.
