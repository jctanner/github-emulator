# Bug: Workflow job tokens cannot call GraphQL mutations

## Summary

A workflow job token's `POST /api/graphql` is refused at the authentication
chokepoint (`api.deps._resolve_user`): `job_permissions.check` maps routes to
scopes by URL, `/api/graphql` names no repository or scope, and an unmapped
write is denied ("endpoint is not covered by any granted permission"). The
GraphQL context swallows the refusal, so the request runs with no user and
every mutation answers "Authentication required", whatever the job declared.

## Expected

As on GitHub, `GITHUB_TOKEN` may call GraphQL, and each mutation is held to
the job's declared permissions in its own repository.

## Actual

No job token can run a GraphQL mutation; queries run anonymously.

## Impact

Workflows that use `gh` commands backed by GraphQL (`gh pr create`,
`gh pr merge`, review-thread resolution) with `GITHUB_TOKEN` fail on the
emulator. `app.graphql.authz.may_write` already holds a job token to its job's
permissions for the review-thread and minimize mutations (github-emulator#20),
so the fix is to let `/api/graphql` through the chokepoint for job tokens and
leave scope checks to the resolvers, starting with those that use `authz`.
