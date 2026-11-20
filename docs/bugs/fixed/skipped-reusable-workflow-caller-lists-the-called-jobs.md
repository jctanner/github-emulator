# Bug: a skipped reusable-workflow caller lists the called jobs, and the run succeeds

Status: fixed 2026-11-14.

## Summary

On GitHub, a job that calls a reusable workflow
(`uses: ./.github/workflows/reusable-dispatch.yml`) and is skipped by its own
`if:` is never expanded. The run lists one job, named after the caller job
(its `name:`, or its key), concluded `skipped`. A run whose jobs were all
skipped concludes `skipped`.

The emulator inlined the called jobs at run creation and ANDed the caller's
condition into each of them. For Fullsend's `fullsend.yaml`, whose `dispatch`
job runs only for `issues` events labelled `go`, an `issues: opened` event
produced a run that:

- listed every called job (`dispatch / route`, `Triage`, `Code` and two
  `Harness run (${{ matrix.agent }})` jobs), each `skipped`;
- concluded `success`, because the run conclusion treated "all skipped" as
  success.

Fullsend's behaviour tests skip runs that concluded `skipped` or `cancelled`.
They read a `success` run whose "Harness run" job was skipped as a failed
dispatch.

A job that `needs:` the caller key (`needs: dispatch`) also never resolved,
because no row carried that key, so it stayed waiting.

## Fix

- `workflow_service._materialize_reusable_jobs` marks each job inlined from
  a top-level call with its caller (`_caller`: key, name, condition, needs).
  `build_job_graph` carries the mark.
- `create_workflow_run` evaluates a caller's `if:` once, when the caller has
  no `needs:`. When it is false, the run gets one skipped row, named after
  the caller and keyed by the caller's key, and none of the called jobs.
  Jobs that need the caller are skipped through it as usual; one that runs
  anyway (`if: always()`) reads `needs.<caller>.result` as `skipped`.
- `workflow_scheduler.check_run_completion` concludes a run `skipped` when
  every job concluded `skipped`. Runs where some jobs ran keep their
  `success` or `failure` conclusion.

When the caller runs, the called jobs and their names are unchanged.

Not covered: a caller with `needs:` is still decided at promotion, where its
condition is ANDed into each called job, so a skip there still lists the
called jobs. The emulator does not mirror check suites or send `workflow_run`
webhooks, so there is no other conclusion to update.

Tests: `tests/actions/test_skipped_reusable_caller.py`.
