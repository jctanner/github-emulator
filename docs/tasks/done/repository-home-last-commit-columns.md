# Repository home: last-commit columns and counts beside the branch selector

## Problem

The repository home page listed file names only. Compared with github.com it had
the branch, tag and commit counts in a row below the toolbar, a bare "Files"
header, and no per-entry commit information.

## Changes

- `GET /api/_ui/repos/{owner}/{repo}/tree?ref=&path=` returns a directory's
  entries (directories first) with each entry's own last commit, plus the
  latest commit for the directory. One `git log -1 -- <path>` per entry, run
  with bounded concurrency. The GitHub-compatible contents API is unchanged.
- The "Files" header is now the latest commit's message (truncated), its short
  hash linking to the commit, and the total commit count linking to the commit
  list.
- Each row shows name, last commit message (truncated with an ellipsis and a
  full-text tooltip) and a relative age (`utils/time.ts`).
- Branch and tag counts sit beside the branch selector; `RepositoryActivity` no
  longer renders a commits link and takes the summary as a prop
  (`useRepositorySummary`), so one request feeds both.
- Directory pages (`CodeBrowserPage`) use the same list component.

## Folder pages (second pass, compared with github.com)

- Branch selector sits at the start of the breadcrumb row; branch/tag counts
  and the total commit count are root-only. Folders show a **History** link
  instead, to `/commits/<ref>?path=<dir>`. `CommitsPage` now honours `?path=`
  (the commits REST API already filtered by path).
- Breadcrumb: every ancestor is a link, the last segment is bold, a trailing
  `/` follows directory paths, and a copy-path button copies the path.
- Column header row (Name / Last commit message / Last commit date) and a `..`
  parent row on folders; the root page has neither, as on github.com.
- Commit bar shows the author name and `hash · age`.
- Ages use weeks from 14 to 27 days and days again until 30. This is fitted to
  two observed github.com values (22 days -> "3 weeks ago", 29 -> "29 days
  ago"), not GitHub's documented rule.
- `src/test/setup.ts` now unmounts between tests; with vitest globals off,
  Testing Library did not, which only mattered once a file had several tests.

## Validation

- Backend: tree endpoint test in `tests/test_browser_repository_api.py`.
- Frontend: typecheck, lint, prettier and vitest (40 tests) pass, including new
  `RepositoryTreeList`, `RepositoryActivity` and `relativeTime` tests.
- Checked in a browser against a scratch local instance, for the repository
  root and a subdirectory. Not yet checked on the deployed stack.

## Follow-ups

- Commit messages are plain text; github.com links the `(#68)` reference.
- No file-tree sidebar, avatars or check-status icon.
- Per-entry `git log` is N subprocesses; fine for directory sizes seen here.
