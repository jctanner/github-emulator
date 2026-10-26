# Pull-request web review submission

## Goal

Let a signed-in user review a pull request from the web UI, as on GitHub:
comment, approve or request changes. The REST review endpoints already
existed, but the web UI could only display reviews posted through the API.

## Implementation

- Files changed carries a "Review changes" popover with a summary and the
  three verdicts. It posts one submitted review through
  `POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews`.
- As on GitHub, the author of a pull request can only comment on it, and
  only an open pull request takes an approval or change request. A comment
  or change request needs a summary.
- The conversation sidebar lists each reviewer's standing verdict. A later
  comment-only review does not replace an earlier approval or change request.
  The sidebar links to Files changed to add a review.
- `POST /admin/users` now stores a bcrypt hash. Before, it stored a SHA-256
  digest that sign-in could never match, so users created there could not
  sign in to the web UI or use basic auth.

## Verification

- Frontend: typecheck, lint and format pass. Vitest runs 66 tests, which
  include approval submission, the author-only-comments rule, the sidebar
  and `reviewerStates`.
- Backend: `tests/test_browser_session_api.py` signs in a user created through
  `POST /admin/users`.
- Live check on the Breadboard cluster in headless Chrome:
  - a second user approved `admin/review-ui-check#1` from Files changed, and
    the review appeared in the conversation with "Approved" in the sidebar;
  - the author saw Approve and Request changes disabled;
  - at 390px width the page did not scroll horizontally.
