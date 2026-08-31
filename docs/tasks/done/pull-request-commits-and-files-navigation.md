# Pull-request commits and files navigation

## Goal

Make the Commits and Files changed tabs on pull-request pages accurate and
navigable, with useful commit and diff views backed by the existing REST APIs.

## Implementation

- Derive PR commit, changed-file, addition, and deletion counts from the Git
  comparison instead of returning hardcoded summary values. Summary requests
  use `git diff --numstat` so they do not generate full patches.
- Added typed response models for PR commits and changed files.
- Added shared, linked PR tabs and dedicated React commit-list and file-diff
  routes.
- Commit rows link to commit details; changed-file headers link to the PR head
  version of each file.
- Render patches with GitHub-like addition, deletion, and hunk coloring.

## Verification

- Full backend suite: 321 passed.
- Frontend typecheck, lint, formatting, 5 tests, and production build passed.
- Rebuilt and deployed with `make host-rebuild-github`.
- Playwright verified `admin/ansible-agent-harness#12` reports 1 commit and 17
  changed files, both tabs navigate successfully, all 17 patches render, and a
  changed-file link resolves against the PR head branch.
