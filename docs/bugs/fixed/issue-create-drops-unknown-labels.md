# Bug: creating or editing an issue drops labels that do not exist yet

Status: fixed 2026-11-14.

## Summary

On GitHub, `POST /repos/{o}/{r}/issues` and `PATCH /repos/{o}/{r}/issues/{n}`
with `"labels": ["new-label"]`, from a caller with push access, create each
label that does not exist yet (color `ededed`) and apply it. The emulator's
`POST /issues/{n}/labels` already did this, but issue create and update
looked each name up and skipped the ones they did not find: the issue came
back with no labels and no label was created. A `labels` entry given as an
object (`{"name": "triage"}`), which GitHub also accepts, failed with a 500.

Fullsend's behaviour tests open issues with labels the repository has not
defined yet, and saw them unlabelled.

GraphQL `createIssue` takes `labelIds`, so there is nothing to create; an
unknown id is already an error there.

## Fix

`src/app/api/issues.py`: `_resolve_labels` resolves the `labels` field for
both create and update through `labels._get_or_create_label`, the helper
`POST /issues/{n}/labels` uses. It accepts names or objects with a `name`
and applies a repeated label once. Update records `labeled` events for the
new labels as before.

Not emulated: GitHub silently drops `labels` for a caller without push
access; the emulator applies them for any authenticated caller, as its
`POST /issues/{n}/labels` does.

## Tests

`tests/test_issue_unknown_labels.py`: create with an existing, a new and a
repeated label; create with a label object; update with a new label,
including its `labeled` event. All three fail before the fix.
