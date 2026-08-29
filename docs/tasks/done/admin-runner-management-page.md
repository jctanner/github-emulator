# Task: Admin Runner Management Page

## Goal

Make GitHub Actions runner registrations and their scope visible in the admin
interface.

## Context

Runner Deployments look site-wide from Kubernetes, but emulator registrations
can be tied to individual repositories or organizations. Administrators need a
single page that makes this distinction and current job assignment obvious.

## Acceptance Criteria

- [x] The admin navigation links to a runners page.
- [x] The page requires an authenticated admin session.
- [x] Repository, organization, and unscoped runners are identified clearly.
- [x] Status, busy state, labels, heartbeat, and current job are visible.
- [x] Repository and Actions job entries link to the corresponding web UI.
- [x] Runner credentials and token hashes are never rendered.
- [x] Focused tests cover authentication and runner rendering.

## Files Likely Involved

- `app/admin/routes.py`
- `app/admin/templates/base.html`
- `app/admin/templates/runners.html`
- `tests/test_admin_runners.py`

## Status

Done

## Notes

- 2026-08-28: Confirmed deployed workers are repository-scoped to
  `fullsend-dev/triage-target` and `fullsend-dev/.fullsend`; the current polling
  API filters queued jobs by the repository in the polling URL.
- 2026-08-28: Added `/admin/runners`, navigation, scope/health/job view models,
  and focused tests. Python compilation and `git diff --check` passed; 24
  focused admin tests passed.
- 2026-08-28: Rebuilt and redeployed with `make host-rebuild-github`. The live
  authenticated page returned HTTP 200, displayed both deployed Fullsend
  registrations and their repository scopes, and contained no credential or
  token-hash material.
