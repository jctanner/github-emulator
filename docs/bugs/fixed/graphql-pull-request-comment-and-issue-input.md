# Bug: GraphQL refuses a pull request comment and gh's createIssue input

Status: fixed 2026-11-15.

## Summary

- **`addComment` on a pull request.** GitHub accepts an Issue or a
  PullRequest node id as the subject. The emulator looked the id up only
  among issues and answered "Subject with id N not found". That breaks:
  - `gh pr close N --comment ...`, which fails before it closes anything;
  - `gh pr comment` through GraphQL.

  `addReaction` had the same lookup.
- **`CreateIssueInput`.** GitHub's input also has `projectIds`,
  `projectV2Ids`, `issueTemplate`, `issueTypeId` and `parentIssueId`.
  `gh issue create` sends `projectIds` even when empty, and the emulator
  refused the request: "Expected value of type 'CreateIssueInput' not to
  include unknown field 'projectIds'".

## Fix

- `addComment` and `addReaction` resolve a PullRequest node id to the pull
  request's backing issue, where its conversation lives.
- `CreateIssueInput` accepts the five fields. Projects, issue templates,
  issue types and sub-issues are not modelled, so their values are ignored.

Tests: `tests/test_graphql_pr_comment_and_issue_input.py`.
