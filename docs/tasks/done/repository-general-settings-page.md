# Task: Repository General Settings Page

## Goal

Provide a GitHub-like settings page for each repository with real persisted
controls rather than a decorative mockup.

## Acceptance Criteria

- [x] Authorized repository administrators see a Settings tab.
- [x] Unauthorized users cannot open or submit repository settings.
- [x] General settings edit description, website, visibility, template mode,
  and feature toggles.
- [x] Repository rename updates both database identity and bare storage.
- [x] Default-branch changes update both metadata and bare repository HEAD.
- [x] The settings sidebar links to branch-protection settings and the existing
  Actions runner data through settings-scoped pages that preserve the sidebar.
- [x] The layout responds cleanly on narrow screens.
- [x] Tests cover rendering, authorization, general settings, default branch,
  and rename behavior.

## Status

Complete

## Notes

- This first pass intentionally omits destructive repository deletion.
- Focused repository settings, creation, REST repository, and organization
  suites pass: 28 tests.
- The settings-scoped Actions runners page preserves the shared sidebar; the
  broader regression set passes 77 tests and live Playwright navigation keeps
  the sidebar visible across every implemented settings destination.
