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
- The same holds further down a chain. `success()` and `failure()` answer
  for every ancestor, and a called job reached through another job is
  counted as its caller, with the call's result (the worst of its called
  jobs' results). The caller is found from the key: called jobs are keyed
  `<caller> / <job>`, and a job id cannot hold ` / `. For `call -> middle ->
  final`, with `final` on `if: success()` and the call holding one
  successful job and one skipped by its own `if:`, `final` now runs. Before,
  only `final`'s own needs were read through the call's alias, so `success()`
  saw the skipped called job and `final` was skipped. Inside a called
  workflow, `deploy / final` counts `deploy / call / *` as the
  `deploy / call` call. A failed called job still skips `middle` and makes
  `success()` false and `failure()` true for `final`, as behind a plain
  failed job.
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
- For the run's own (top-level) callers, "the calling workflow's inputs" are
  the run's inputs (`inputs` in the run's expression context, from the
  `workflow_dispatch` or `workflow_call` payload), the same values the called
  jobs render their `with:` against. Before, the alias used `{}` there, so a
  compound expression such as `${{ format('{0}-x', inputs.ver) }}` gave the
  called job `1.0-x` but `needs.<caller>.outputs` `-x`. For a caller inside
  a called workflow, they are that workflow's inputs, not the run's.
- Calls nested at any depth resolve each `with:` in the terms of the
  workflow it is written in. The alias keeps the `with:` of every enclosing
  call (`parent_inputs_chain`, outermost first), and each called job keeps
  the same chain (`_call_inputs_chain`). Both are rendered one level at a
  time: the rendered inputs of one level are the `inputs` context of the
  next, starting from the run's inputs. Before, the alias kept only the
  innermost calling workflow's inputs and rendered them against the run's,
  and the called jobs rendered all levels' merged inputs in one pass against
  the run's. With the run's `ver=run`, `outer` called with `ver=2`, `mid`
  with `ver=${{ format('{0}-mid', inputs.ver) }}` and `inner` with
  `tag=${{ format('v{0}', inputs.ver) }}`, a job in `mid` read `run-mid`
  for `inputs.ver` and `vrun-mid` for `needs.inner.outputs.tag`; they are now
  `2-mid` and `v2-mid`.
- A bare `${{ inputs.x }}` in a called workflow is substituted at
  materialization only when the passed value is a literal. A value that is
  itself an expression is in the calling workflow's terms, so the reference
  is kept and reads the job's rendered inputs. Before, the expression was
  copied in and then rendered against the called workflow's inputs: with
  `ver=${{ format('{0}-mid', inputs.ver) }}`, `${{ inputs.ver }}` in `mid`
  became `2-mid-mid`.
- A caller that needs another caller (A → B → C, B with
  `x: ${{ needs.A.outputs.o }}`) keeps the aliases of the callers it needs
  (`needs_aliases`, in any job order; a needs cycle is cut). Its `with:` is
  rendered with `needs.A` built from A's called jobs, the entry a plain
  dependent of A reads. Before, C's called jobs read `needs.B` from B's
  alias, which rendered `needs.A` against plain job rows only, so
  `needs.B.outputs` was `""`, C's jobs received an empty input, and
  `needs.C.outputs` was empty too. B's own called jobs already rendered
  `inputs.x` correctly from their own `needs.A` entry.

[contexts]: https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#context-availability

Tests: `tests/actions/test_reusable_caller_needs.py`,
`tests/actions/test_skipped_reusable_caller.py`.
