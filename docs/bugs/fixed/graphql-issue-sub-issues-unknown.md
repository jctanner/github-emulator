# Bug: GraphQL refuses Issue.subIssues

Status: fixed 2026-11-15.

## Summary

GitHub's `Issue` type has `subIssues` (a connection) and `parent`. Before a
code agent acts on an issue, its scripts ask
`issue(number: N) { subIssues(first: 1) { totalCount } }`. The emulator
answered "Cannot query field 'subIssues' on type 'Issue'". The scripts read
that error as zero sub-issues, so nothing failed. But every run logged the
error, and the "has sub-issues" path could not be reached.

## Fix

`Issue.subIssues` returns an empty connection and `Issue.parent` returns
null. Sub-issues are not modelled, so the query validates and reports none.

Test: `tests/test_graphql_issue_sub_issues.py`.
