# Bug: job conditions cannot use status functions, and compare like Python

Status: fixed 2026-11-08.

## Summary

The job-level `if` evaluator raised "unsupported function cancelled" on
`success()`, `failure()` and `cancelled()`, and an evaluation error counts as
false. Fullsend's reusable dispatch gates its harness job on
`!cancelled() && (... fromJSON(needs.harness-dispatch.outputs.matrix).include[0] != null)`,
so the job was always skipped, under the name "Harness run ()", and no
harness ever ran (Breadboard BT-U1: pr-ping, hooks-loaded, reaction
notifications).

Two related differences from GitHub:

- A job was skipped whenever a job it needs had failed or been skipped,
  whatever its condition said. On GitHub a condition without a status
  function is `success() && (...)`, but one that names a status function
  (`always()`, `!cancelled()`, `failure()`) is evaluated anyway.
- Comparison was Python's: case-sensitive, `null != ''`, and no `<`, `>`,
  `<=`, `>=`. GitHub compares strings case-insensitively and mixed types as
  numbers (`null`, `''` and `0` are equal). `contains`, `startsWith` and
  `endsWith` are case-insensitive too.

## Fix

- `workflow_expressions`: the four status functions (`job_status`, from the
  results of every job the job depends on, directly or not); `_compare` with GitHub's coercion and the
  ordering operators; case-insensitive string functions; array `contains`
  by equality.
- `workflow_scheduler`: a job whose condition names a status function is
  promoted once all its needs have finished, successfully or not, and its
  condition decides; one without is skipped as before.

## Evidence

`tests/actions/test_job_status_functions.py`: Fullsend's harness condition
with and without a matrix, and with the input unset; each status function;
the comparison table; a failed need skipping a plain or `success()` job and
queueing `failure()` and `always()` jobs; `failure()` seeing a failed
ancestor behind a skipped job; the harness-run job queued after its dispatch
job.
