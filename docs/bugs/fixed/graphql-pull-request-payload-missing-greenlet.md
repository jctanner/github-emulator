# Bug: updatePullRequest, closePullRequest, and reopenPullRequest crash on their payload

## Summary

`updatePullRequest` with a title or body, and `closePullRequest` and
`reopenPullRequest` whenever they changed state, failed with
`greenlet_spawn has not been called` (SQLAlchemy MissingGreenlet) after
committing: the change was saved, but the mutation returned an error.

## Cause

Building the payload read attributes the commit had expired, which lazily
loads inside the async resolver. `updatePullRequest` refreshed only the issue's
relationships, leaving its server-set `updated_at` expired. The close and
reopen mutations refreshed the pull request and then the issue; refreshing the
issue reloads its `pull_request` relationship and expired the pull request's
eager loads (`auto_merge`) again. Existing tests only changed assignees, which
does not update the issue row, and nothing tested close or reopen.

## Fix

Refresh the issue whole, then the pull request (and, for update, the issue's
relationships). Covered by `tests/test_graphql_integration_writes.py`.
