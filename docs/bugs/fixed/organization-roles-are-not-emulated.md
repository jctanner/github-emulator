# Bug: organization roles are not emulated, and org membership grants no repository permission

Status: fixed 2026-11-07 (migration 0017, `app/services/org_roles.py`).

## Summary

GitHub lets an organization give a member a permission on every repository
it owns through an organization role. The predefined all-repository roles
are read, triage, write, maintain and admin. A role covers repositories
created after it was assigned, so a fleet of throwaway repositories can share
the same people without a collaborator grant on each one. Fullsend's
behaviour suite relies on this: its write and triage test actors hold
all-repository roles in the pool organization, whose slot repositories are
deleted and recreated per lease (fullsend #7777, `hack/setup-new-e2e-org.sh`).

The emulator had none of it:

- `GET /orgs/{org}/organization-roles` was 404.
- A member's repository permission ignored the organization entirely:
  - the permission endpoint, `can_write_contents` and git push answered only
    for the repository's `owner_id` user, a collaborator or an App's bot;
  - an organization owner had no permission on repositories other members
    created.

Two related defects surfaced on the way:

- **Wrong vocabulary.** `GET /repos/{o}/{r}/collaborators/{u}/permission`
  answered in the emulator's internal names: `permission` and `role_name`
  were `"push"` or `"pull"`. GitHub's `role_name` is admin, maintain,
  write, triage or read, and its legacy `permission` is admin, write or
  read. Fullsend's dispatch (`has_repo_permission`) reads `role_name`, so it
  read a push collaborator as having no access.
- **Wrong id compared.** `can_read`'s organization-member check compared the
  organization's id with `repository.owner_id`, which is the user who
  created the repository. A member of the organization could not read its
  private repositories unless the two ids happened to coincide.

## Fix

- **`app/services/org_roles.py`.** The five predefined roles have GitHub's
  names (`all_repo_write`, …) and fixed ids (8132–8136). The service also
  works out what a member holds through the organization: admin for an
  organization owner, otherwise the highest of their roles.
- **`org_role_assignments` table (migration 0017).** Removing a membership
  or deleting the organization removes the member's roles, as leaving does
  on GitHub.
- **Routes in `api/orgs.py`.**
  - `GET /orgs/{org}/organization-roles`, `GET …/{role_id}`,
    `GET …/{role_id}/users` and `GET …/{role_id}/teams`. Teams are not
    emulated, so the last returns an empty list.
  - `PUT` and `DELETE /orgs/{org}/organization-roles/users/{username}/{role_id}`,
    and `DELETE /orgs/{org}/organization-roles/users/{username}`.
  - All of them are for organization admins and site admins only. A role can
    be assigned only to an active member; otherwise the response is 422.
- **`repository_access.repository_permission`** answers for a person in one
  place. It returns admin for the `owner_id` user, and otherwise the highest
  of the collaborator permission and what they hold in the repository's
  organization (`organization_id`). It feeds:
  - the permission endpoint, now in GitHub's vocabulary with
    `user.permissions`;
  - `can_write_contents`, which the contents API and git push now both use;
  - the admin checks in collaborators, branch protection, merge readiness,
    and repository settings (`PATCH /repos/{o}/{r}`, which had checked only
    the creator and site admins). Deletion keeps that narrower check: GitHub
    also requires the `delete_repo` token scope, which the emulator does not
    enforce.
  A site admin's override stays in those checks and is not reported as a
  repository permission.
- **`can_read`** matches membership on `organization_id`.

## Evidence

- `tests/test_org_roles_api.py` covers:
  - the role catalogue by name;
  - admin-only reads;
  - assignment that needs an org admin and a member, and is idempotent;
  - a role covering a repository created after it;
  - write reaching content writes while triage does not;
  - the higher of collaborator and role winning;
  - removal of a role or the membership taking access away;
  - an org owner administering another member's repository;
  - a member reading the organization's private repository. This test fails
    with the old `owner_id` comparison;
  - an admin role reaching repository settings while write does not.
- `tests/test_database_migrations.py`: 0017 on a database at 0016.
- `tests/test_collaborators_api.py`: a push collaborator now reads as
  `write`/`write`.
