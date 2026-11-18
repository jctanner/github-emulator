# Bug: Actions write routes do not check the caller's repository role

Status: fixed 2026-11-18.

## Summary

GitHub requires a role on the repository for the Actions write routes:

- write access to dispatch, cancel or re-run a workflow, and to upload or
  delete an artifact;
- admin access (the owner, for a personal repository) to manage Actions
  secrets and to change Actions settings, which are repository settings.

The emulator checked only that the caller was signed in. A reader, or a user
with no role on the repository, could make these changes. Cancel also took
any run id, including a run of another repository.

## Fix

`_require_repository_role` in `src/app/api/actions.py` answers 403 unless the
person holds the role: `Must have write access to this repository.` or `Must
have admin rights to Repository.`. A site admin passes. A job's GITHUB_TOKEN
and an App installation token keep the permission checks they already get at
authentication.

| Route | Role |
|---|---|
| `POST .../actions/workflows/{id}/dispatches` | write |
| `POST .../actions/runs/{id}/cancel`, `.../rerun` | write |
| Artifact upload, `DELETE .../actions/artifacts/{id}` | write |
| `PUT`/`DELETE .../actions/secrets/{name}` | admin |
| `POST`/`PATCH`/`DELETE .../actions/variables...` | admin |
| `PUT .../actions/permissions`, `.../permissions/workflow` | admin |

Cancel now acts only on a run of the repository in its path; another run id
is 404.

GitHub's docs give admin for repository secrets and settings. One docs page
lists write access for variables; the emulator uses admin for variables too,
the same as the repository's Actions settings page.

Tests: `tests/test_actions_write_permissions.py`.
