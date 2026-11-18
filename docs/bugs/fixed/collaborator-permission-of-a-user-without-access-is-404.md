# Bug: a user without access gets 404 from the collaborator permission endpoint

Status: fixed 2026-11-14.

## Summary

On GitHub, `GET /repos/{owner}/{repo}/collaborators/{username}/permission`
returns 200 for any user that exists. If the user is not a collaborator, has no
organization role and is on no team, a private repository answers:

```json
{"permission": "none", "role_name": "", "user": {"login": "...", "permissions": {"admin": false, "maintain": false, "push": false, "triage": false, "pull": false}, "role_name": ""}}
```

A public repository answers `"permission": "read"` and `"role_name": "read"`
instead, because anyone can read it. Only a login that names no user is 404.

The emulator returned 404 `Not Found` for every user without access. A client
could not tell "no access" apart from "no such user". This includes an
organization member whose base permission grants nothing.

## Fix

`get_collaborator_permission` in `src/app/api/collaborators.py` now handles a
person that `repository_permission` gives no permission:

- on a public repository, it reports read (`pull`);
- otherwise, it reports `none` with an empty `role_name` and every
  `permissions` flag false.

Collaborator, organization-role and owner answers are unchanged. An App's bot
without a collaborator row is still 404: GitHub does not report an
installation's access on this endpoint, and existing tests pin that.

Two tests in `tests/test_org_roles_api.py` pinned the old 404 for a member
without a role on a public organization repository. They now expect `read`.
Their checks that such a member cannot write are unchanged.

Tests: `tests/test_collaborator_permission_none.py`.
