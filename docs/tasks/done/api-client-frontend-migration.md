# Task: API-Client Frontend Migration

## Goal

Execute `docs/plans/api-client-frontend-migration.md` milestone by milestone,
retaining the Jinja UI for parity validation until explicit retirement
approval.

## Milestone Status

- [x] M0 route, interaction, authentication, fixture, and API-gap inventory.
- [x] M1 legacy UI remount.
- [x] M2 typed frontend foundation.
- [x] M3 Playwright parity harness.
- [x] M4 read-only repository experience.
- [x] M5 repository mutations.
- [x] M6 Actions and repository settings.
- [x] M7 administrative UI.
- [x] M8 cutover validation and approved legacy retirement.

## M8 Validation

- [x] Frontend types, lint, formatting, unit tests, and production build.
- [x] Complete backend regression suite.
- [x] Desktop and narrow live route-manifest validation.
- [x] Upstream `actions/runner` registration and job-execution smoke.
- [x] Breadboard image rebuild and Kubernetes rollout.
- [x] Frontend architecture, ownership, and local Honcho workflow documented.
- [x] Visual/semantic candidate differences reviewed and accepted.
- [x] Explicit approval to remove `/ui-legacy` and Jinja compatibility code.

## Retirement Evidence

- Approval was given on 2026-08-30 after the React UI became canonical.
- Removed `/ui-legacy`, Jinja routes/templates/static assets, the parity-only
  Playwright harness, and the broad `/admin` browser redirect.
- Unknown React routes now render a normal not-found page rather than linking
  back to the retired implementation.
- Added a Smart HTTP regression proving an `admin`-owned repository can serve
  `git-upload-pack` discovery without redirect interception.
- Frontend typecheck, lint, unit tests, and production build pass.
- 318 backend tests outside the independently stale Alembic-head assertions
  pass; the two migration tests still expect `0001_baseline` while the current
  migration head is `0002_repo_org_owner`.

## Status

Done
