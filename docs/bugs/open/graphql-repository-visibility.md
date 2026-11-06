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
