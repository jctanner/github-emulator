# Bug: A job token's GraphQL `viewer` is the run's actor

Status: fixed 2026-11-20 (github-emulator#94).

## Summary

On GitHub a workflow's `GITHUB_TOKEN` is an installation token of the GitHub
Actions App: GraphQL `viewer` is the `github-actions[bot]` Bot, and REST
`GET /user` answers 403. Scripts that print or branch on the authenticated
identity, to tell whether they run as a bot, rely on this.

A job token authenticates as the run's actor, the user who triggered the run.
REST `/user` has answered 403 for a job token since #92, but GraphQL `viewer`
returned the actor, with the actor's login, id and repositories.

## Fix

- For a job token, `viewer` is the `github-actions[bot]` Bot
  (`__typename: Bot`, login `github-actions[bot]`). The account is created
  the first time a job token asks, as App bot accounts are
  (`auth_service.ensure_actions_bot`), in a session of its own so a query's
  session holds no write.
- Its fields describe the bot account, and its `repositories` and
  `contributionsCollection` hold only what that account owns: nothing about
  the actor.
- Other credentials are unchanged.

## Schema

GitHub declares `viewer: User!` and still answers a Bot for a GITHUB_TOKEN.
A schema can return a Bot only through an abstract type, so `viewer` now
returns `Viewer`, an emulator interface with every User field, implemented by
`User` and `Bot`. A user's viewer query, `viewer { id name repositories }`
included, reads as before. `Bot` gains the User fields as a result; gh's bot
detection (`... on User { id }`) is unaffected.

The viewer Bot's login keeps the `[bot]` suffix, as GitHub's does; an
author's Bot still shows the bare App slug
(`graphql-app-authors-are-typed-as-users.md`).

## Not changed

- A job token's writes are still authored by the run's actor:
  `job-token-writes-are-authored-by-the-run-actor.md`.
- A job with a permissions block is still refused GraphQL at the
  authentication chokepoint, so its queries run anonymously:
  `job-tokens-cannot-use-graphql.md`.

Tests: `tests/test_graphql_job_token_viewer.py`.
