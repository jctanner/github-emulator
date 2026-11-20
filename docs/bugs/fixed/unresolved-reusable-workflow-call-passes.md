# Bug: a reusable-workflow call that cannot be inlined passes

Status: fixed 2026-11-14.

## Summary

The emulator inlines a reusable-workflow call into the called jobs. When it
could not, it left a placeholder job with no steps, and a job with no steps
passes. That happened for:

- a cross-repository call (`owner/repo/.github/workflows/x.yml@ref`) to a
  repository the emulator does not store;
- a call nested deeper than the limit;
- a call that loops back to itself.

A run whose whole pipeline was one such call looked green. On GitHub the
same run fails.

## Fix

`workflow_service._materialize_reusable_jobs` builds the placeholder with
`_reusable_placeholder`, which gives it one step that fails with an
`::error::` saying why. For a missing repository the message says the call
resolves only against repositories stored in the emulator, and that the
repository has to be pushed first. The placeholder no longer carries `uses`,
`with` or `secrets`.

GitHub fails such a run without assigning a runner. The placeholder is
therefore never queued: `create_workflow_run` concludes it `failure` when
the run is created, or, for a placeholder with `needs:`, the scheduler
concludes it when its needs finish and its `if:` holds
(`workflow_service.fail_refused_job`). Its one step is recorded as the
failed step, and the reason is written to the job's log. Queued, it waited
forever on a stack whose runners do not offer its `runs-on` labels
(`ubuntu-latest` by default).

Tests: `tests/actions/test_reusable_unresolved.py`.
