# Users and organizations share ids

Status: fixed 2026-11-18 (migration 0018, `app/models/account.py`). Issue #40.

## Symptom

A user and an organization could have the same id. On a fresh instance the
admin user and the first organization were both 1. A repository's REST
`owner.id` is the organization's id for an organization-owned repository and
the user's id otherwise, and OIDC `repository_owner_id` carries the same
value, so a relying party that pins an owner only by id could match a user
whose id equals the pinned organization's.

## Cause

`users.id` and `organizations.id` were separate autoincrement sequences. On
GitHub both are accounts and share one id space.

## Fix

- `account_ids` (`app/models/account.py`) is the one sequence. A
  `before_insert` listener on `User` and `Organization` inserts a row there
  and gives the new account that id. A row created with an explicit id
  records it too, so an id the other table holds fails the insert.
- Migration 0018 creates the table, moves each organization whose id is at
  or below the highest user id ever handed out to a new id above every user
  and organization id handed out, rewrites the columns that reference it
  (`repositories.organization_id`, `org_memberships`, `org_role_assignments`,
  `teams`, `events`, `webhooks`, `runners`, `org_variables`, and any other
  reflected foreign key to `organizations.id`), and seeds `account_ids` so no
  id is reused across the two tables.
- REST `owner.id`, GraphQL `databaseId` and webhook `repository.owner.id`
  read the row ids, so they now name one account each
  (`tests/test_account_ids.py`).
- GraphQL `Organization.repositories` filtered on `Repository.owner_id`
  (the creating member) equal to the organization's id, which only matched
  because the ids collided. It filters on `organization_id` now
  (`test_organization_repository_list`).

Stored JSON payloads (events, webhook deliveries) keep the ids they were
sent with.

## Not covered

- GraphQL `Repository.owner` resolves the repository's creating user even
  for an organization-owned repository, so its `databaseId` there is the
  user's, not REST `owner.id`. That is a separate gap.
- The OIDC `check_run_id` claim (from open PR #27) is the job id, but no check
  run is created for a job, so `GET /check-runs/{id}` does not resolve it.
