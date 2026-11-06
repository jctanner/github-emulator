# Bug: GraphQL reads ignore an installation token's content permissions

## Summary

REST holds an installation token to its permissions per route: a token with
only `metadata: read` cannot list issues. GraphQL checks that the token covers
the repository (`app.graphql.authz.visible`) but not which permissions it has,
so such a token reads issue bodies, pull request bodies and comments, reviews,
and refs through `repository { issues ... }`, `pullRequest`, `search`, and
`node`. `PullRequest.reviewThreads` already requires `pull_requests` read.

## Expected

Each data-bearing field requires the App permission that governs it
(`issues`, `pull_requests`, `contents`), as REST does.

## Impact

A narrowly minted installation token reads more through GraphQL than through
REST.
