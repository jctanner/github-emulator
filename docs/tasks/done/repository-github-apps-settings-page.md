# Task: Repository GitHub Apps Settings Page

## Goal

Add the GitHub Apps subsection under repository Settings → Integrations and
show which App installations grant access to the current repository.

## Acceptance Criteria

- [x] The persistent settings sidebar contains an Integrations section.
- [x] GitHub Apps links to a settings-scoped page and remains selected there.
- [x] The page lists only installations selecting the current repository.
- [x] Each entry shows App name, slug, account, installation ID, and permissions.
- [x] Site administrators can follow a Configure link to the existing
  installation administration page.
- [x] Repositories without installations receive an explicit empty state.
- [x] Unauthorized users cannot inspect repository App installations.
- [x] Tests cover inclusion, repository isolation, rendering, and authorization.

## Status

Complete

## Notes

- GitHub App installation access remains separate from direct collaborator
  access. This page makes that distinction visible in the repository UI.
- Email notifications appears as a non-functional sidebar item for layout
  parity; no notification settings were added by this task.
- The broader web, Apps, collaborator, repository, organization, branch, and
  merge-readiness regression set passes: 103 tests.
- `make host-rebuild-github` deployed the page; Playwright verified Fullsend
  Triage installation #2 and its three granted permissions on the live
  `admin/ansible-agent-harness` repository.
