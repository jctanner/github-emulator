# Bug: a matrix written as an expression runs as one job with no matrix

Status: fixed 2026-11-08.

## Summary

`strategy.matrix: ${{ fromJSON(needs.harness-dispatch.outputs.matrix) }}` is
a string when the run is created, and `expand_matrix` ran such a job once,
with an empty matrix, logging "Unsupported dynamic matrix". Fullsend's
reusable dispatch computes its harness matrix in one job and fans it out in
`harness-run`. With the status-function fix in place that job ran, but:

- its name rendered as "Harness run ()", so the behaviour harness never
  found "Harness run (<agent>)";
- its steps read empty `matrix.*` values, so the mint action failed with
  "repos input is required";
- its concurrency group rendered as `fullsend-harness--<repo>--` for every
  harness, so harness jobs of different agents, issues and runs superseded
  one another.

This was the rest of Breadboard BT-U1. In the T2 tier it failed almost every
dispatch scenario.

Two related defects:

- A job that `needs:` a matrix job became ready when the first instance
  finished, and its `needs.<key>` result was whichever instance came last.
  On GitHub it waits for every instance, and the result is failure if any
  instance failed.
- An include-only matrix produced an extra empty combination, because the
  product of no vectors is one empty tuple. Includes were always appended
  as new combinations rather than merged into the combinations they extend.
  A name that already used `matrix.*` had the values appended a second time.

## Fix

- `workflow_service`:
  - `dynamic_matrix_expression`, `matrix_combinations` (GitHub's
    include/exclude rules) and `matrix_job_name`;
  - a dynamic matrix on a job with no needs is evaluated at creation;
  - a dynamic matrix on a job with needs is kept in `pending_render`,
    together with the raw name and concurrency;
  - `build_needs_context` combines the instances of one key.
- `workflow_expressions.evaluate_expression_value`: evaluates a whole
  `${{ }}` expression to its value rather than to text.
- `workflow_scheduler`:
  - `_expand_dynamic_matrix` evaluates the matrix at promotion. The first
    instance takes the waiting row and the others get rows of their own,
    each with its own name, steps and concurrency group. An empty or
    non-mapping matrix fails the job, as on GitHub.
  - Needs are aggregated per job key, across instances.
  - The stranded-job check compares job keys, not display names.
  - When an expansion fails, readiness is computed again, so jobs behind
    the failed one are decided without waiting for another dispatch.
- Runner labels may read `matrix.*`. They are rendered at creation for jobs
  without needs, and per instance at promotion otherwise.
- A dynamic matrix on a job with no needs that is empty or malformed fails
  the job, as at promotion; it does not run once with no matrix.

With expansion working, the harness job failed one step later with
"matrix agent and job index must be valid". Two more gaps were behind that:

- There was no `strategy` context, so `strategy.job-index` rendered empty.
  Each instance now has `fail-fast`, `job-index`, `job-total` and
  `max-parallel`, for static and dynamic matrices alike, rendered into its
  steps server-side as `matrix.*` is. The runner's own `strategy` context
  data is still 0 of 1.
- The evaluator did not parse object filters (`include.*.agent`, `[*]`),
  and an evaluation error renders as empty. Filters now yield a list, with
  later accessors applied to each element, as on GitHub. A dotted name
  token no longer ends in a dot, so the filter's `.` and `*` are tokens of
  their own.

A second review pass tightened these:

- A job's `if` is decided before its matrix, so a skipped job with an empty
  matrix is skipped, not failed.
- A filtered value is a `_Filtered` list, so it stays filtered through
  parentheses. Properties that are present but null are kept.
- `bind_server_context`, which binds the server-side half of a step
  condition that also reads `steps.*`, binds a path together with its
  accessors (`labels.*.name`, `labels[0].name`). A list or object goes to
  the runner as `fromJSON('...')`. Before, the path alone was bound, and
  `true.*.name` reached the runner.
- `fail-fast` and `max-parallel` are evaluated when they are expressions.
- Dotted names may again have hyphen-led segments.

Tests: `tests/actions/test_dynamic_matrix.py`, plus the object-filter cases
in `tests/actions/test_job_status_functions.py`.
