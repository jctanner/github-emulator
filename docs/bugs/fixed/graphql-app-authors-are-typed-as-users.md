# Bug: GraphQL types an App's author as a User

Status: fixed 2026-11-18.

## Summary

GitHub types `author` as the `Actor` interface: a `User`, or a `Bot` whose
login is the App slug without REST's `[bot]` suffix. gh asks for
`author{login,...on User{id,name}}`. It reports an author without a User
id as `{"is_bot": true, "login": "app/<login>"}`, which scripts use to
recognise an App.

The emulator typed every author as a `User`, so gh reported an App's pull
request as written by a person named `<slug>[bot]`.

## Fix

- An `Actor` interface, which `User` implements, and a `Bot` type. The
  requested-reviewer `Bot`, a login-only stub before, is the same type.
- Issue, pull request, review and review comment authors resolve to a
  `Bot` for an account whose type is Bot.
- REST is unchanged.
- A Bot's `id` resolves through `node(id:)`.

Compatibility: `author` now returns `Actor`, which has `login`, `avatarUrl`
and `url`. A query that selects a User-only field there (`id`, `name`,
`databaseId`, `email`) needs `... on User { }`, as on GitHub.

Tests: `tests/test_graphql_bot_author.py`.
