# Task: Admin-created organizations get an owner

## Goal

An organization created through the admin API has an owner, as on GitHub Enterprise, instead of
having no members at all.

## Context

`POST /admin/api/organizations` (`src/app/api/admin_frontend.py:191`, `add_organization`) creates
the `Organization` row and nothing else: no `OrgMembership`. Nobody can then create a repository
in the org: `POST /orgs/{org}/repos` requires an active membership
(`src/app/api/repos.py:496-503`) and returns 403 "Must be an organization member", even for the
site admin who made it. There is no API to add a member afterwards (see
`org-membership-api.md`), so the org can only be deleted and recreated with `POST /api/v3/orgs`,
which makes its caller an admin member.

GitHub Enterprise's equivalent, `POST /admin/organizations`, requires `login` and `admin`: the
login of the user who will manage the org. That user becomes its owner. It also takes an optional
`profile_name`.

Found bootstrapping a fresh emulator for Markov's github-workflow-example
(`vokramd/github-workflow-example/workflows/bootstrap-github.yaml`, which now uses `POST
/api/v3/orgs` instead).

## Acceptance Criteria

- [x] `POST /admin/api/organizations` accepts `admin` (a user login) and adds that user as an
      active `admin` member. When it is left out, the calling site admin becomes the owner, so
      the org is never left with no members. Unknown logins get 422.
- [x] Optionally, a GHES-compatible `POST /api/v3/admin/organizations` (`login`, `admin`,
      `profile_name`), site admins only.
- [x] The admin UI's create-organization form, if there is one, sends or picks the owner.
- [x] Tests: the named owner can create a repository in the new org; without `admin` the caller
      is the owner; an unknown login is rejected.
- [x] Orgs created earlier with no members can be repaired once `org-membership-api.md` is done;
      no migration is needed.

## Files Likely Involved

- `src/app/api/admin_frontend.py`
- `src/app/models/organization.py`
- the admin frontend's organization form
- `tests/test_admin_frontend_api.py`

## Status

Complete (2026-10-24).

## Validation

- `POST /admin/api/organizations` takes an optional `admin` login. That user, or else the calling
  site admin, becomes the org's active admin; an unknown login gets 422 and creates nothing.
- `POST /api/v3/admin/organizations` (GHES: `login`, `admin`, `profile_name`) is site-admin only
  (403 otherwise) and returns 422 for a missing `admin`, an unknown `admin`, or an existing login.
- `src/app/services/org_service.py` `create_organization` creates the org together with its owner;
  `POST /orgs`, the admin API and the GHES endpoint all use it.
- The admin page's create-organization form has an optional owner field
  (`src/frontend/src/pages/AdminPage.tsx`).
- `tests/test_admin_frontend_api.py`: four tests: the named owner can create a repository, the
  default owner is the caller, an unknown owner is rejected, and the GHES endpoint's success and
  error cases. Full suite passing; frontend typecheck, lint, format and tests passing.
- Orgs created earlier with no members can be repaired with
  `PUT /orgs/{org}/memberships/{username}` as a site admin (covered in
  `tests/test_org_memberships_api.py`).
