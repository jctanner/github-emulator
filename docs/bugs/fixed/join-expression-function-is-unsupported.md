# Bug: `join()` is not supported in workflow expressions

Status: fixed 2026-11-08.

## Summary

The server's expression engine had no `join()`. Any expression using it
failed to render, with "unsupported function join", and came out empty.

Fullsend's dispatch passes labels to its routing script as:

```
PR_LABELS: ${{ join(github.event.pull_request.labels.*.name, ',') }}
ISSUE_LABELS: ${{ join(github.event.issue.labels.*.name, ',') }}
```

Both were always empty. A human's pull request reaches the fix stage only
when it carries the `fullsend-fix` label, so it never did.

In Breadboard's behaviour-test T2 tier, `fix-authorization.feature` ("only
the registered review bot's changes-requested review starts fix") failed with
`"fix" agent was not dispatched`. The fix for the review state's casing was
already in place.

The cause was found by reproducing the review in a scratch repository and
replaying the step:

- The routing script, run with the payload's values, routed to `fix`.
- The step's `env:`, rendered with the emulator, gave `PR_LABELS=''`.

## Fix

`join(array, separator)` is implemented as on GitHub. The separator defaults
to `,`, items are converted to strings, and a non-array argument is returned
as a string.

Tests: `tests/actions/test_expression_join.py`.

## Not covered

- The runner's step-condition evaluator still lacks `join`, `fromJSON` and
  `toJSON`. They are needed only where an expression reads `steps.*`, and
  Fullsend has no such use.
- Observed, not changed: `pull_request` and `pull_request_review` event
  payloads also carry an `issue` object, which GitHub does not send.
  Fullsend routes on the event name first, so it is harmless there.
