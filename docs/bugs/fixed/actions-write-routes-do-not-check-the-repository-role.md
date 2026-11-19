# Bug: Actions write routes do not check the caller's repository role

Status: fixed 2026-11-18.

## Summary

GitHub requires a role on the repository for the Actions write routes:

- write access to dispatch, cancel or re-run a workflow, to upload or delete
  an artifact, and to manage Actions variables;
- admin access (the owner, for a personal repository) to manage Actions
  secrets and to change Actions settings, which are repository settings.

A job's GITHUB_TOKEN has no permission for secrets, variables or repository
administration, so GitHub answers it with 403 `Resource not accessible by
integration` on those routes.

The emulator checked only that the caller was signed in. A reader, or a user
with no role on the repository, could make these changes, and a job token
with `actions: write` reached the secrets, variables and settings routes.
Cancel also took any run id, including a run of another repository.

## Fix

`_require_repository_role` in `src/app/api/actions.py` answers 403 unless the
person holds the role: `Must have write access to this repository.` or `Must
have admin rights to Repository.`. A site admin passes. An App installation
token keeps the permission checks it already gets at authentication. A job
token keeps its checks on the run and artifact routes, and gets `Resource not
accessible by integration` on the secrets, variables and settings routes.

| Route | Role | Job token |
|---|---|---|
| `POST .../actions/workflows/{id}/dispatches` | write | its `actions` permission |
| `POST .../actions/runs/{id}/cancel`, `.../rerun` | write | its `actions` permission |
| Artifact upload, `DELETE .../actions/artifacts/{id}` | write | its `actions` permission |
| `POST`/`PATCH`/`DELETE .../actions/variables...` | write | 403 |
| `PUT`/`DELETE .../actions/secrets/{name}` | admin | 403 |
| `PUT .../actions/permissions`, `.../permissions/workflow` | admin | 403 |

Cancel now acts only on a run of the repository in its path; another run id
is 404.

Tests: `tests/test_actions_write_permissions.py`.
