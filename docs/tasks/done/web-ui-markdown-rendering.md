# Web UI Markdown rendering

## Goal

Issue and pull-request descriptions, comments, reviews, review comments and
README files render as GitHub-flavored Markdown instead of raw text.

## Implementation

- `src/frontend/src/utils/markdown.ts` renders with `marked` (GFM: tables,
  task lists, strikethrough, autolinks) and sanitizes the HTML with
  `DOMPurify` before it is inserted. Both are pinned to exact versions.
  - Comments and descriptions treat a newline as a line break, as GitHub
    does; README files do not.
  - Fenced code in a language highlight.js knows is highlighted with the
    same highlighter and theme as the code browser.
  - `http(s)` links open in a new tab with `rel="noopener noreferrer"`.
- `components/Markdown.tsx` is used by the issue and pull-request pages,
  `IssueComments`, `PullReview` and `pullReviewEntries`.
- The code browser renders a `README.md` or `README.markdown` as Markdown and
  any other README as plain text.
- `.markdown-rendered` styles follow GitHub's spacing for headings, lists,
  task lists, code, tables, quotes and rules.

Not done: `#123` issue references and `@user` mentions are not linked, and the
server's `POST /markdown` endpoint is still its basic line-to-paragraph
version.

## Evidence

- `src/utils/markdown.test.ts`: GFM elements, line breaks, fence
  highlighting, stripping of scripts, event handlers and `javascript:` links,
  and the new-tab rule. Frontend: 77 tests pass; lint and typecheck clean.
- Deployed with `make host-rebuild-github`. In headless Chrome a comment on
  `admin/review-ui-check#2` rendered its heading, table, two task checkboxes,
  highlighted Python and a new-tab link, and its `<script>` did not run.
