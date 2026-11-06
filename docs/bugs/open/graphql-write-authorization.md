# Bug: GraphQL mutations do not check write access on visible repositories

## Summary

Since the visibility fix, a GraphQL mutation cannot reach a repository the
caller cannot see. On a repository the caller *can* see (any public one),
most issue and pull request mutations still check only that the caller is
signed in: any user can `updateIssue`, `closeIssue`, `reopenIssue`,
`addLabelsToLabelable`, `mergePullRequest`, `closePullRequest`, or
`updatePullRequest` on someone else's repository. Installation and job tokens
are not held to their permissions for these mutations either (REST scopes them
by URL, which `/api/graphql` lacks).

## Expected

As on GitHub: opening issues, pull requests, and comments needs read access;
editing, closing, labelling, and merging needs triage or write access (or
authorship, for an issue's own title and body); integration tokens are held to
`issues`, `pull_requests`, and `contents` grants. `app.graphql.authz.may_write`
already implements the credential side, and the review-thread, minimize, and
`createCommitOnBranch` mutations use it.

## Impact

On a shared emulator, any account can modify or merge another account's
public pull requests and issues through GraphQL. Deciding the per-mutation
rules changes behaviour existing clients may rely on, so it is left for a
decision.
