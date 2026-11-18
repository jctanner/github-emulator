# Bug: a comment posted while its repository is deleted answers 500

Status: fixed 2026-11-15.

## Summary

A test tore down its repository (`DELETE /repos/{owner}/{repo}`) while an
agent was still posting a comment on issue 1 of that repository. The comment
route had already found the issue, so it went on to commit. During the commit,
the cross-reference hook looked up the comment's issue to find its repository,
found no row, and raised `NoResultFound`. The client got a 500.

## Fix

The hook now skips a comment whose issue no longer exists: there is nothing
to cross-reference. The comment commits as it would have if it had landed just
before the delete.

Test: `test_a_comment_whose_issue_was_deleted_meanwhile_commits` in
`tests/test_cross_references.py`.
