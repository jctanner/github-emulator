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
- `AddCommentPayload.subject` is GitHub's `Node` interface, and returns the
  subject the caller named: the PullRequest for a PullRequest id. `Node` is
  now an interface that Issue, PullRequest, Repository, User and Bot
  implement, as on GitHub, so `subject { id }` and `node(id:) { id }` work
  without a fragment. It was a union before.
- A missing subject and one in a repository the caller cannot see fail with
  the same error, which names the id the caller gave.
- `CreateIssueInput` accepts the five fields. Projects, issue templates,
  issue types and sub-issues are not modelled, so their values are ignored.

Tests: `tests/test_graphql_pr_comment_and_issue_input.py`.
