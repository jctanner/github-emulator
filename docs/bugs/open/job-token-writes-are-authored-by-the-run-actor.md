# Bug: A job token's writes are authored by the run's actor

## Summary

On GitHub, what a workflow writes with `GITHUB_TOKEN` (an issue, a comment, a
label, a pull request, a review) is authored by `github-actions[bot]`. In the
emulator a job token authenticates as the run's actor
(`api.deps._resolve_user` returns `run.actor`), so REST and GraphQL writes
made with it are authored by the user who triggered the run.

## Expected

A job token's writes are authored by `github-actions[bot]`, the account its
GraphQL `viewer` already is (`graphql-job-token-viewer-is-the-run-actor.md`,
`auth_service.ensure_actions_bot`).

## Impact

Workflows and tools that tell bot activity from a person's by the author (to
skip their own comments, say) see the actor instead of the bot. Changing it
moves authorship on every write route, so it was left out of
github-emulator#94.
