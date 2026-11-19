# Bug: an issue's `closedByPullRequestsReferences` is always empty

Status: fixed 2026-11-14.

## Summary

On GitHub, a pull request whose description carries a closing keyword
(`close`, `closes`, `closed`, `fix`, `fixes`, `fixed`, `resolve`,
`resolves`, `resolved`, case-insensitive, with `#n` or `owner/repo#n`) and
whose base is the default branch is linked to the issue. GraphQL
`Issue.closedByPullRequestsReferences` lists those pull requests. Pull
requests closed without merging are left out unless `includeClosedPrs: true`.
The field also takes `orderByState` and `userLinkedOnly`.

The emulator returned an empty connection for every issue, and rejected
`includeClosedPrs` and `orderByState` as unknown arguments. An open pull
request with `Fixes #1` in its description did not show on issue #1.
Fullsend's behaviour tests read this field to find the pull request for an
issue.

## Fix

The field is computed at query time from pull-request descriptions, with the
same rules `closingIssuesReferences` already used (see
`merged-pr-does-not-close-linked-issues.md`): a pull request is listed when
its own `closingIssuesReferences` would name the issue.

- `includeClosedPrs` (default false) adds pull requests closed without
  merging. Merged ones are always listed.
- `orderByState` lists open, then merged, then closed pull requests, oldest
  first within each.
- `userLinkedOnly` returns nothing, since links made by hand in the web UI
  are not emulated.
- Pull requests in repositories the caller cannot see are left out.

Commit-message keywords still only take effect at merge time, as before.

## Tests

- `tests/test_closing_issues.py`
  (`test_issue_lists_pull_requests_whose_body_closes_it`,
  `test_closed_pull_requests_are_listed_only_on_request`,
  `test_issue_lists_a_cross_repository_closing_pull_request`).
