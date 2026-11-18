# Bug: creating a label or ref that already exists does not fail as on GitHub

Status: fixed 2026-11-14.

## Summary

Clients tell "it is already there" from other failures by the body of the
422, not the status alone. Fullsend's behaviour tests create their labels on
every run and treat `errors[].code == "already_exists"` as "nothing to do".

- **Labels.** GitHub answers a duplicate `POST /repos/{o}/{r}/labels` with
  422 `{"message": "Validation Failed", "errors": [{"resource": "Label",
  "code": "already_exists", "field": "name"}], "documentation_url": ...}`,
  and compares names case-insensitively: `BUG` clashes with `bug`, and
  `GET /labels/Bug` finds `bug`. The emulator answered 422
  `{"message": "Label already exists"}` with no `errors`, compared names
  case-sensitively (so `BUG` was created next to `bug`), and let
  `PATCH /labels/{name}` rename one label onto another's name.
- **Release assets.** The duplicate-name check meant to send the same
  shape, but passed it as an `HTTPException` detail, so the body came out as
  `{"message": {"message": "Validation Failed", "errors": [...]}}` with no
  top-level `errors`.
- **Git refs.** GitHub answers `POST /repos/{o}/{r}/git/refs` for an
  existing ref with 422 `{"message": "Reference already exists"}` and leaves
  the ref alone. The emulator ran `git update-ref`, which moves an existing
  ref, so it answered 201 and silently repointed the branch.

## Fix

- `src/app/api/labels.py`: every label lookup by name compares
  case-insensitively (`_name_is`). Create, and a rename onto another label's
  name, raise `ValidationError` with the `already_exists` error. A rename
  that only changes a label's own case is allowed. `PUT /issues/{n}/labels`
  applies `bug` and `Bug` in one request once. SQLite's `lower()` folds
  ASCII letters only, so a name also matches its exact spelling, and a
  name with non-ASCII capitals is found. A database written before this
  can hold both `bug` and `BUG`: a lookup prefers the exact spelling and
  never fails on the second row.
- `src/app/api/releases.py`: the duplicate-asset error is a
  `ValidationError`, so `errors` is at the top level.
- `src/app/api/git_refs.py`: `create_ref` refuses an existing ref with
  "Reference already exists", and passes an empty old value to
  `git update-ref` so git refuses one created in between.

Checked and left alone: duplicate repositories, organizations, users and
forks (a fork that exists is returned, as on GitHub); duplicate release tags
and milestone titles are accepted, unlike GitHub, and are not covered here.

## Tests

`tests/test_duplicate_create_errors.py`: duplicate label, label case
folding (create, get, list), rename onto another label and a case-only
rename, duplicate release asset, and creating an existing ref (422, ref
unchanged). All five fail before the fix.
