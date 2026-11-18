# Bug: GraphQL `closeIssue` does not record the state reason as GitHub does

Status: fixed 2026-11-14.

## Summary

On GitHub, `CloseIssueInput.stateReason` is an `IssueClosedStateReason`
enum (`COMPLETED`, `NOT_PLANNED`, `DUPLICATE`) and defaults to `COMPLETED`.
`duplicateIssueId` names the issue this one duplicates and closes it as
`DUPLICATE`. REST then reports the reason in lowercase
(`"state_reason": "not_planned"`), and GraphQL `Issue.stateReason` returns
the `IssueStateReason` enum (`NOT_PLANNED`).

The emulator typed `stateReason` as `String` on both the input and
`Issue`. It stored whatever text it was given, so REST showed
`"NOT_PLANNED"` instead of `"not_planned"`. A close without a reason stored
none, where GitHub stores `completed`. Reasons written by REST or by a merge
(`"completed"`) came back from GraphQL in lowercase. An enum literal
(`stateReason: NOT_PLANNED`) was rejected, and `duplicateIssueId` was an
unknown field. Fullsend's behaviour tests close issues as not planned and
read the reason back.

## Fix

- A new `IssueClosedStateReason` enum types `CloseIssueInput.stateReason`.
  The mutation stores the REST form (`not_planned`), defaulting to
  `completed`.
- `CloseIssueInput.duplicateIssueId` is accepted. It must name another
  issue, and closes as `duplicate` unless `stateReason` says otherwise. The
  link itself (`Issue.duplicateOf`, the timeline event) is not emulated.
- `Issue.stateReason` returns the `IssueStateReason` enum, mapped from the
  stored form.

Moving from `String` to an enum changes the schema. Clients that pass the
reason in a variable as `"NOT_PLANNED"` (as the gh CLI does) are
unaffected. A quoted literal (`stateReason: "NOT_PLANNED"`) or an unknown
value is now a validation error, as on GitHub. No existing test relied on
the string form.

## Tests

- `tests/test_issue_state_reason.py`
  (`test_close_issue_records_the_state_reason`,
  `test_close_issue_state_reason_is_an_enum`,
  `test_close_issue_as_a_duplicate_of_another`).
