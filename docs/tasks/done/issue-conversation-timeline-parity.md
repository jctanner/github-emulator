# Issue conversation timeline parity

## Goal

Match GitHub's issue conversation controls and history: author actions live in
the comment header's three-dot menu, while label additions and removals appear
inline in chronological order with comments.

## Implementation

- Added durable issue events with actor and label snapshots, plus migration
  `0003_issue_events` for existing databases.
- Added `GET /repos/{owner}/{repo}/issues/{issue_number}/events` with the
  GitHub-compatible `labeled` and `unlabeled` response shape.
- Record events for POST, PUT, PATCH, and DELETE issue-label mutations.
- Interleave issue events and comments by creation time in issue and pull-request
  conversations.
- Moved description/comment author controls into header overflow menus and kept
  site-admin moderation access.

## Verification

- Full backend suite: 321 passed.
- Frontend typecheck, lint, formatting, and production build passed.
- Frontend component suite: 4 passed.
- Live deployment rebuilt with `make host-rebuild-github`.
- Playwright verified a label addition and removal inline in issue 10, verified
  Edit/Delete inside the comment header menu, and removed the temporary comment.
