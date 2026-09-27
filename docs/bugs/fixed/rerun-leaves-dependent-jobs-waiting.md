# Bug: re-running a workflow leaves every dependent job waiting forever

## Summary

`POST /repos/{owner}/{repo}/actions/runs/{run_id}/rerun` creates a new run
whose first-tier jobs are queued and served, and whose dependent jobs stay
`waiting` until the run is cancelled. Observed 2026-09-27 on
`phase2/fresh-target` run 1589: `Route` completed on `fullsend-agent-runner`
at 15:00:54, and `Triage`, `Code`, `Review`, `Fix`, `Retro`, `Prioritize`
and `Harness run` were still `waiting` twelve minutes later while the runner
polled and received 204. The original run 1588 had dispatched `Triage`
normally from the same workflow.

## Cause

`rerun_workflow` in `src/app/api/actions.py` copies each job's `name`,
`steps`, `labels` and `needs`, and nothing else. Dependents are promoted by
`dispatch_ready_jobs` in `src/app/services/workflow_service.py`, which
matches a job's `needs` against `job.job_key or job.name`. `needs` names
workflow keys (`route`); the copied job carries only its display name
(`Route`) and no `job_key`, so no dependency ever resolves. The copy also
drops `pending_render`, `outputs_config`, `permissions`, `concurrency_group`
and `workflow_name`-adjacent state that the first run had, so even a job
that were promoted would run with unrendered conditions and steps.

## Fix

Copy `job_key`, `pending_render`, `outputs_config`, `permissions` and
`concurrency_group` onto the new jobs, or better, rebuild the run through
`create_workflow_run` from the stored workflow and trigger payload so a
rerun takes the same path as the first run. Add a test that reruns a
two-job workflow and asserts the second job is dispatched after the first
completes; `tests/actions/test_execution.py` has the fixtures.

## Workaround

File a new triggering event instead of rerunning; cancel the stuck run so
its `in_progress` status does not linger in the admin Actions view.

## Fixed 2026-09-27

`rerun_workflow` no longer copies job rows. It detects the workflow at the
run's commit, materializes its reusable workflows and creates the run from
the stored trigger payload - the same path a fresh event takes - with
`run_attempt` incremented. `tests/actions/test_consolidation_bug_fixes.py`
reruns a two-job workflow, completes the first job and asserts the second
is queued.
