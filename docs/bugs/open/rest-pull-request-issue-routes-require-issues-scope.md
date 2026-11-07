# Bug: REST refuses `pull_requests: write` on a pull request's issue routes

## Summary

GitHub's fine-grained permission tables accept either Issues or Pull requests
write for the issue routes when the issue is a pull request: commenting
(`POST /repos/{o}/{r}/issues/{n}/comments`), labelling, assigning, and
changing state. The emulator's route map (`job_permissions._SCOPE_BY_SEGMENT`,
reused for installation tokens by `deps._check_installation_token_route`) maps
every `/issues/` path to `issues` alone. A token with `pull_requests: write`
and no `issues` permission is refused there with 403.

GraphQL accepts either scope for a pull request's backing issue
(`mutations._issue_scopes`), as GitHub does; REST is the stricter of the two.

## Expected

For `/issues/{n}/...` writes where issue `n` is a pull request, accept
`issues` or `pull_requests` write. This needs the issue looked up at the
chokepoint, which the path-only map cannot do today.

## Impact

A role App granted only `pull_requests: write` cannot comment on or label
its pull requests through REST, though it can through GraphQL (`gh pr
comment`) and on GitHub.
