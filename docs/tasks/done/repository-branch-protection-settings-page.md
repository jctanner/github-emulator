# Task: Repository Branch Protection Settings Page

## Goal

Make Settings → Branches a repository-administration surface for persisted
branch protection instead of linking to the read-only code branch list.

## Acceptance Criteria

- [x] The repository settings sidebar links to `/settings/branches`.
- [x] Authorized repository administrators can list exact-branch rules.
- [x] Unauthorized users cannot view or submit branch settings.
- [x] Administrators can enable or remove protection for an existing branch.
- [x] The editor configures required reviews, required status checks, admin
  enforcement, linear history, force pushes, and branch deletion.
- [x] Rules use the same persisted models consumed by REST APIs and merge
  readiness evaluation.
- [x] Tests cover rendering, authorization, creation/update, and removal.

## Status

Complete

## Notes

- The existing emulator branch-protection implementation supports exact branch
  names. Wildcard rules remain explicitly unsupported by its REST API.
- The normal repository `/branches` page remains the read-only code browser.
- General, Branches, and Actions runners share the same settings sidebar; the
  standalone Actions runner view remains available from the Actions tab.
- The broader web, repository, organization, branch API, and merge-readiness
  regression set passes: 75 tests.
- `make host-rebuild-github` deployed the page, and Playwright validated the
  live protected and unprotected branch states.
