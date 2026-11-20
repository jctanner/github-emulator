# Bug: two requests adding the same new label answer 500

Status: fixed 2026-11-16.

## Summary

Adding a label that did not exist yet (issue create or update, or
`POST`/`PUT /repos/{owner}/{repo}/issues/{number}/labels`) looked the label up,
then inserted it. Two requests adding the same new label at once both missed
the lookup, and the second INSERT failed the `(repo_id, name)` unique
constraint: `UNIQUE constraint failed: labels.repo_id, labels.name`, answered
500.

## Fix

`_get_or_create_label` in `src/app/api/labels.py` inserts with
`INSERT ... ON CONFLICT DO NOTHING` and reads the label back, so both
requests get the same label.

## Evidence

- `tests/test_label_create_race.py`: fails on the previous code.
- A concurrent write benchmark: 5 such 500s in 4 one-minute runs before, none
  after.
