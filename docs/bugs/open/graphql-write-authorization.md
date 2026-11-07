# Bug: GraphQL mutations do not check a user's role on visible repositories

## Summary

Since the visibility fix, a GraphQL mutation cannot reach a repository the
caller cannot see. On a repository the caller *can* see (any public one), the
issue and pull request mutations check only that a *user* credential is signed
in: any user can `updateIssue`, `closeIssue`, `reopenIssue`,
`addLabelsToLabelable`, `mergePullRequest`, `closePullRequest`, or
`updatePullRequest` on someone else's repository.

REST does the same: `PATCH /repos/{owner}/{repo}/issues/{n}`, the pull
request routes, and the label routes check authentication and integration
scope but not the user's repository role. Only contents, git-data, and ref
writes require write access (`deps.require_contents_write`).

## Fixed so far

Integration credentials are now held to their own grant on GraphQL, as REST
holds them by URL (`authz.integration_may_write`, used by
`mutations._writable_or_none`). An installation or job token needs `issues`
write for issues, `pull_requests` write for pull requests (including merge and
auto-merge, matching the emulator's REST route map), and either for a pull
request's backing issue (its labels, comments, reactions, and state).
Otherwise the mutation fails with FORBIDDEN, "Resource not accessible by
integration", and writes nothing. Tests:
`tests/test_graphql_integration_writes.py`.

GitHub requires `contents: write` to merge; the emulator's REST and GraphQL
both accept `pull_requests: write` instead.

## Remaining

As on GitHub: opening issues, pull requests, and comments needs read access;
editing, closing, labelling, and assigning needs triage or write access (or
authorship, for an issue's own title, body, and state); merging needs write
access. This would have to apply to REST and GraphQL together, and it changes
behaviour existing clients and fixtures may rely on (accounts editing issues
in repositories they are not collaborators on), so it is left for a decision.

## Impact

On a shared emulator, any account can modify or merge another account's
public pull requests and issues through either API.
