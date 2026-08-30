# Task: Add PR Files Changed jump-to-file menu

## Goal

Add a file navigation menu to the pull request Files changed view so reviewers
can quickly jump to a changed file in large diffs.

## Context

GitHub's PR Files changed page exposes a toolbar above the diff with controls
such as "Changes from all commits", "File filter", "Conversations", "Jump to",
and "Diff settings". The key missing review workflow in the emulator was the
"Jump to" file menu: a compact list of changed files that links to each file's
diff section.

The emulator Files changed tab rendered changed-file boxes and patches, but did
not have file menu or jump navigation.

## Acceptance Criteria

- [x] The PR Files changed tab renders a left-side file navigation control.
- [x] The control lists all changed files in the PR.
- [x] Each menu item links to the corresponding file diff on the page.
- [x] Each file diff has a stable anchor target.
- [x] The menu shows useful file metadata, at minimum filename and status.
- [x] The menu includes additions/deletions counts when available.
- [x] The feature works with existing `diff_files` produced by PR base/head
      comparison.
- [x] Tests cover the rendered menu, file anchors, and jump links.

## Files Changed

- `src/app/web/templates/pull_detail.html`
- `src/app/web/templates/base.html`
- `src/app/web/static/css/web.css`
- `tests/test_pulls_api.py`

## Status

Done.

## Implementation Notes

- Added a no-JavaScript sidebar labeled "Jump to file" beside the PR Files
  changed diff list.
- Each changed-file menu row displays status, filename, additions, and
  deletions.
- Each row links to a stable per-file anchor such as `#file-1`.
- Each rendered file diff box now includes the corresponding anchor target.
- The Files changed tab uses a wider page container than the default PR view,
  with the file menu taking roughly 20% of the available width.
- The sidebar stays visible while scrolling on desktop and collapses above the
  diff on narrow screens.
- Kept the scope to navigation only; no source/rich diff toggles or full
  line-numbered diff rendering were added.

## Verification

Ran:

```bash
uv run pytest tests/test_pulls_api.py::test_pr_web_files_tab_renders_diff -v
uv run pytest tests/test_pulls_api.py -v
git diff --check
```

Result:

- Targeted Files changed test passed.
- Full PR suite passed: 20 passed, 1 warning.
- Diff whitespace check clean.
