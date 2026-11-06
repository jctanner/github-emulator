# Bug: GraphQL `Repository.issues` includes pull requests

## Summary

`repository { issues { nodes { title } } }` returns pull requests as well as
issues. GitHub's `issues` connection holds issues only; pull requests are in
`pullRequests`. Pull requests are stored as issues with a linked pull request
row, and the connection (`src/app/graphql/types/repository.py`) does not
exclude them.

## Impact

Clients that count or iterate a repository's issues through GraphQL see pull
requests among them. Found while testing GraphQL repository visibility.
