# Task: Repository Collaborator Settings Page

## Goal

Provide a GitHub-like Settings → Collaborators page for managing direct
repository access while preserving the repository settings navigation shell.

## Acceptance Criteria

- [x] Collaborators is an active link in the persistent settings sidebar.
- [x] The page summarizes repository visibility and direct-access count.
- [x] Repository owners and site administrators can add existing users.
- [x] Permissions support pull, triage, push, maintain, and admin roles.
- [x] Existing collaborator permissions can be changed.
- [x] Direct collaborators can be removed.
- [x] Unknown users and invalid permissions produce useful errors.
- [x] Unauthorized users cannot view or mutate collaborator settings.
- [x] REST collaborator mutations enforce repository-admin access.

## Status

Complete

## Notes

- The emulator currently models direct user collaborators. Organization team
  access is not presented as functional until repository-team permissions are
  implemented.
- The repository owner is implicit and is not counted as a direct collaborator,
  matching the empty-state count shown by GitHub.
- The broader web, collaborator, repository, organization, branch, and
  merge-readiness regression set passes: 91 tests.
- `make host-rebuild-github` deployed the page; Playwright validated its
  persistent settings navigation and the live repository's three direct-access
  entries.
