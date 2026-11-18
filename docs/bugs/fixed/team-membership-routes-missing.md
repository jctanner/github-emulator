# Bug: team membership routes are missing

Status: fixed 2026-11-18.

## Summary

`GET`, `PUT` and `DELETE /orgs/{org}/teams/{team_slug}/memberships/{username}`
did not exist, so they answered 404. A token mint that lets the members of
one team call its status endpoint checks the caller with the `GET`, and
every such check failed.

## Fix

- `GET` answers 200 with `role` and `state` for a team member, and 404
  otherwise. The user themselves, active members of the organization and
  site admins can see it. A secret team is visible only to its members,
  org admins and site admins, so to anyone else it answers 404.
- `PUT` lets org admins and site admins add a user or change their team
  role (`member` or `maintainer`). GitHub invites a user who is not yet in
  the organization. The emulator has no team invitations, so that case
  answers 422 rather than creating a membership that does not exist.
- `DELETE` lets org admins and site admins remove a user, and answers 204.

Tests: `tests/test_team_memberships.py`.
