# Pull-request web conversation controls

## Goal

Make pull-request pages useful for the normal conversation workflow: read,
write, edit, and close a pull request from the emulator UI.

## Implementation

- Reused the existing issue-comment records for pull-request conversation
  comments, matching GitHub's issue-comment API model for pull requests.
- Added an authenticated PR comment composer and author/site-admin edit forms.
- Added PR close and reopen controls with repository write/merge permission
  checks.
- Dispatch `issue_comment` events for new/edited comments and
  `pull_request_target` events for close/reopen actions.
- Refresh ORM objects after state changes before event serialization because
  async SQLAlchemy expires committed attributes.

## Verification

- `tests/test_pulls_api.py`: 22 passed.
- Regression coverage exercises add, edit, close, and reopen through the web
  routes and verifies the API-visible PR state.
