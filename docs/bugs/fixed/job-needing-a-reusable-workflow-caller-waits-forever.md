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
- Nested calls re-prefix those keys at each level.
- A skipped caller is one skipped job under its own key
  (`skipped-reusable-workflow-caller-lists-the-called-jobs.md`), so a need on
  it points at that job. Its `needs.<caller>` is then that job's own entry
  (`result: skipped`); the entry built from called jobs, which were never
  created, is dropped. Before, it overwrote the real entry with `result: ''`.
- Only needs on the run's own (top-level) callers are pointed at a skipped
  caller's job (`_caller_needs`). A caller in a called workflow can share a
  top-level caller's key; a need on it used to be pointed at the skipped
  top-level job instead.

Tests: `tests/actions/test_reusable_caller_needs.py`,
`tests/actions/test_skipped_reusable_caller.py`.
