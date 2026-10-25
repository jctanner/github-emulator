# Task: Org membership API

## Goal

Implement GitHub's org membership endpoints so a client can add, promote and remove members of an
organization, and check its own or another user's membership.

## Context

The emulator only adds org members in one place: `POST /api/v3/orgs` (`src/app/api/orgs.py:52`)
makes its caller an `admin` member. Nothing else creates an `OrgMembership`, and `GET
/orgs/{org}/members` (`src/app/api/orgs.py:179`) is read-only. Creating a repository in an org
requires an active membership (`src/app/api/repos.py:496-503`, "Must be an organization member"),
so an org with no members, or one whose members are missing, cannot be fixed through the API.

This came up bootstrapping a fresh emulator for the Markov github-workflow-example project
(`vokramd/github-workflow-example/workflows/bootstrap-github.yaml`). An org created through the
admin API (see `admin-organization-owner.md`) had no members, so `reseed-github` could not create
its repository there, even as the site admin. The only way out was to delete and recreate the
org. With this API the bootstrap can make sure its admin is an org owner, and can make the bot an
org member if that is ever wanted.

## Endpoints

Follow the GitHub REST docs (Orgs → Members) for request and response shapes:

- `PUT /orgs/{org}/memberships/{username}` with `{"role": "admin" | "member"}` (default
  `member`): add or change a membership. Org admins, and site admins, may call it.
  - Real GitHub creates a `pending` invitation for a new member. Whether the emulator should make
    the membership `active` straight away is a decision for this task: active is what test
    automation needs, so it may be the default, with pending as an option. Record the choice.
- `GET /orgs/{org}/memberships/{username}`: one membership (`state`, `role`, `organization`,
  `user`).
- `DELETE /orgs/{org}/memberships/{username}` (and `DELETE /orgs/{org}/members/{username}`):
  remove a member.
- `GET /user/memberships/orgs` and `GET /user/memberships/orgs/{org}`: the caller's own
  memberships.
- `PATCH /user/memberships/orgs/{org}` with `{"state": "active"}`: accept a pending membership,
  if pending memberships are supported.
- Optional: `GET /orgs/{org}/members/{username}` (204 for a member, 404 if not), and filtering
  `GET /orgs/{org}/members` by `role`.

## Acceptance Criteria

- [x] The endpoints above, with GitHub's status codes: 403 for callers who are not org admins
      or site admins, 404 for an unknown org or user, and 422 for an invalid role.
- [x] A site admin can add members, owners included, to an org that has no members.
- [x] A user added as `member` can create a repository in the org (the
      `repos.py` membership check passes).
- [x] The org's last admin cannot be removed or demoted, as on GitHub; or record why the
      emulator allows it.
- [x] Tests in `tests/` covering add, promote, read, remove, the caller's own memberships, and
      the permission errors.
- [x] README, or the API coverage notes if there are any, list the new endpoints.

## Files Likely Involved

- `src/app/api/orgs.py`
- `src/app/models/organization.py` (`OrgMembership`)
- `src/app/api/repos.py` (membership check, for the tests)
- `tests/` (a new `test_org_memberships_api.py` alongside `test_collaborators_api.py`)

## Status

Complete (2026-10-24).

## Decision

Memberships added with `PUT` are `active` at once; see
`docs/decisions/ADR-0003-org-memberships-are-active-on-add.md`. Pending rows are still reported,
and `PATCH /user/memberships/orgs/{org}` accepts them.

The last active admin cannot be removed or demoted: both return 422.

## Validation

`tests/test_org_memberships_api.py`: nine tests covering add and read, the default role and
promotion, the `role` filter on `GET /orgs/{org}/members`, a new member creating an org
repository, removal through both `DELETE` routes, the last-admin guard, the caller's own
memberships, accepting a pending membership, a site admin repairing an org with no members, and
the 401, 403, 404 and 422 errors. Full suite passing.

## Notes

The endpoints are in `src/app/api/orgs.py`. They appear in the generated OpenAPI contract
(`src/frontend/src/api/schema.d.ts`), which is the emulator's API coverage list; the README has no
endpoint inventory to update.
