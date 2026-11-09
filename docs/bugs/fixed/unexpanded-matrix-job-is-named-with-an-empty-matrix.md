# Bug: an unexpanded matrix job is named with an empty matrix

Status: fixed 2026-11-08.

## Summary

A job whose matrix comes from a `needs` output is one row until its needs
finish, when it expands into one instance per combination. The emulator
rendered that row's name at creation, against an empty matrix.
`Harness run (${{ matrix.agent }})` became `Harness run ()`.

If the job's `if:` was then false, it was skipped under that name. GitHub
shows a matrix job that never expanded under its unrendered name.

Fullsend's behaviour suite tells "no harness was scheduled" from a scheduled
instance by that name. In Breadboard's behaviour-test T2 tier,
`fix-authorization.feature` ("only the registered review bot's
changes-requested review starts fix") failed with:

```
expected an empty harness matrix, but job "Harness run ()" was scheduled
```

The job had been skipped.

## Fix

The row of a dynamic-matrix job keeps its unrendered name. Its instances are
named, as before, when the matrix expands at promotion.

Tests: `tests/actions/test_dynamic_matrix.py`
(`test_a_matrix_job_skipped_before_it_expands_keeps_its_unrendered_name`).
