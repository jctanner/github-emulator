# Bug: a job that needs a reusable-workflow caller waits forever

Status: fixed 2026-11-14.

## Summary

On GitHub, a job can name a reusable-workflow caller in `needs:` and read
`needs.<caller>.result` and `needs.<caller>.outputs`; the outputs are the
callee's `on.workflow_call.outputs`.

The emulator inlines a caller as its called jobs, so the caller is no job in
the run. A job with `needs: <caller>`:

- waited for a job that never existed, and stayed `waiting`;
- had nothing to read in `needs.<caller>.outputs`.

## Fix

- A need on a caller becomes a need on every job the call inlined.
- The dependent carries the caller's called-job keys and the callee's
  `on.workflow_call.outputs`. At promotion the scheduler builds
  `needs.<caller>` from them: the result is the worst of the called jobs'
  results, and the outputs are evaluated with a `jobs` context of the called
  jobs.
- The dependent waits for every called job, but its implicit `success()`,
  and `success()`/`failure()` in its `if:`, read the call's result, not each
  called job's. On GitHub a call whose jobs succeeded except one skipped by
  its own `if:` succeeds, and a `needs: <caller>` dependent with no `if:`
  runs. Before, the scheduler saw the skipped called job as an unsuccessful
  need and skipped the dependent. A call with a failed or cancelled job is
  still unsuccessful, so its dependent is still skipped. Plain needs are
  judged as before.
- Nested calls re-prefix those keys at each level.
- A caller inside a called workflow keeps its own alias inside the outer
  caller's alias (`callers`). The outer call's outputs can then read
  `jobs.<nested caller>.outputs`: that entry is built the same way, from the
  nested caller's called jobs and its own `on.workflow_call.outputs`. Before,
  the outer `jobs` context held only the flattened called jobs
  (`<nested caller> / <job>`), so such an output rendered as `""`.
- A skipped caller is one skipped job under its own key
  (`skipped-reusable-workflow-caller-lists-the-called-jobs.md`), so a need on
  it points at that job. Its `needs.<caller>` is then that job's own entry
  (`result: skipped`); the entry built from called jobs, which were never
  created, is dropped. Before, it overwrote the real entry with `result: ''`.
- Only needs on the run's own (top-level) callers are pointed at a skipped
  caller's job (`_caller_needs`). A caller in a called workflow can share a
  top-level caller's key; a need on it used to be pointed at the skipped
  top-level job instead.
- A call's outputs are evaluated with the contexts GitHub allows in
  `on.workflow_call.outputs.<output_id>.value`: `github`, `jobs`, `vars` and
  `inputs` ([contexts reference, context availability][contexts]). The alias
  carries the call's resolved inputs (the caller's `with:` plus the callee's
  declared defaults), nested callers' aliases included, and the scheduler
  passes the run's `github` and `vars`. Before, only `jobs` was there, so
  `${{ inputs.version }}` rendered as `""` even when the caller passed it or
  it had a default. `with:` values still holding an expression are rendered
  at promotion against `github`, `vars`, `needs` and the calling workflow's
  inputs. `secrets` is not in that context, as on GitHub.

[contexts]: https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability

Tests: `tests/actions/test_reusable_caller_needs.py`,
`tests/actions/test_skipped_reusable_caller.py`.
