# Bug: GraphQL repository and node lookups ignore repository visibility

## Summary

REST hides a private repository from callers who may not read it
(`api.deps._refuse_hidden_repository`) and holds an installation token to the
repositories it was minted for. The GraphQL `repository(owner:, name:)` and
`node(id:)` resolvers (`src/app/graphql/queries.py`) do neither, so an
anonymous caller or an unrelated user can read a private repository's issues,
pull requests, and comments through GraphQL.

## Expected

As on GitHub, `repository` returns null (with a NOT_FOUND error) and `node`
resolves nothing for a repository the caller cannot see.

## Actual

The private repository's data is returned.

## Impact

Private repository contents leak through `/api/graphql`. The review-thread
fields added for github-emulator#20 check visibility themselves
(`app.graphql.authz.may_read`), but the pull request and its other fields do
not. The fix belongs at the `repository` and `node` resolvers, using
`app.graphql.authz.may_read`.

## Fix

`app.graphql.authz.visible` is the GraphQL counterpart of REST's gate: the
caller must be able to read the repository (`repository_access.can_read`) and
an installation token must cover it. It is cached per request and applied
wherever GraphQL reaches a repository from outside it:

- `repository(owner:, name:)` returns null, exactly as for a repository that
  does not exist, so existence is not revealed either;
- `node(id:)` for a `Repository`, `Issue`, or `PullRequest`;
- `search` for repositories and issues, filtered before the page is cut;
- `User.repositories` and `Organization.repositories` (also reached through
  `repositoryOwner`), with `totalCount` counting visible repositories only;
- `PullRequest.closingIssuesReferences`, whose references may name an issue in
  another repository.

Fields of an object already reached (an issue's comments, a pull request's
threads) inherit its visibility. `tests/test_graphql_repository_visibility.py`
covers each entry point for anonymous callers, outsiders, collaborators, the
owner, organization members, and installation tokens.

A review (Codex CLI) found the same leak through mutations and nested
fields, also fixed:

- every issue and pull request mutation (`updateIssue`, `closeIssue`,
  `reopenIssue`, `addComment`, `addLabelsToLabelable`, `createIssue`,
  `createPullRequest`, `mergePullRequest`, the auto-merge pair,
  `closePullRequest`, `reopenPullRequest`, `updatePullRequest`) and
  `addReaction` treat a target in a hidden repository as missing: the same
  not-found error, no write, no payload;
- `createIssue` and `updateIssue` accept only labels and milestones of the
  issue's own repository (another repository's would attach, then reveal, its
  name and description);
- `PullRequest.headRepository` and `headRepositoryOwner` are null for a head
  repository the caller cannot see;
- `createCommitOnBranch` reports a hidden repository as `NOT_FOUND`, like a
  missing one, and writes under the presented credential's `contents` grant;
- `createRepository` refuses another owner (for anyone but a site admin)
  before its name-conflict check, which had revealed another owner's private
  repository names and created repositories in their namespace;
- `search` reads matches in batches and stops when a page of visible results
  is full, instead of loading every match.

A second review pass confirmed those and found two more, also fixed:
`addReaction` now fails alike for a missing subject and a hidden one (it had
accepted any id, so the difference revealed hidden issues), and an issue's or
pull request's `labels` and `milestone` follow only associations in its own
repository, so a cross-repository association saved before input validation
is not read.
