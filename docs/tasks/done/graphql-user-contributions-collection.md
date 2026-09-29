# Task: GraphQL `user.contributionsCollection`

## Goal

Serve GitHub's contribution calendar for a user, so Org Pulse's team-tracker
module can compute per-person activity against the emulator the way it does
against github.com.

## Why

Org Pulse fetches, per person, `user(login:) { contributionsCollection {
contributionCalendar { totalContributions weeks { contributionDays { date
contributionCount } } } } }`, batched with aliases. The gh CLI never asks for
contributions, so the emulator's GraphQL had none; the Breadboard stack
wants Org Pulse pointed at the emulator (see breadboard `docs/org-pulse.md`),
and this is the one GraphQL shape it needs.

## Acceptance Criteria

- [x] `User.contributionsCollection(from, to)` with `startedAt`, `endedAt`,
      the four totals, `hasAnyContributions`, and a `contributionCalendar`
      of weeks of days with `date`, `contributionCount` and `weekday`.
- [x] Counts follow GitHub's rules on the rows the emulator has: indexed
      commits by the user's email (case-insensitive) or login, de-duplicated
      by sha; issues opened; pull requests opened; submitted reviews. Days
      in UTC.
- [x] The calendar starts on the Sunday on or before `from`, so the first
      week may be partial; `weekday` is 0 for Sunday.
- [x] The window defaults to the year ending now and is refused beyond a
      year, with GitHub's message.
- [x] The exact aliased query team-tracker sends is served.

## Status

Complete (2026-09-29).

## Validation

`tests/test_graphql_contributions.py`: four tests covering the counting
rules (including the fork copy, the other author, the pre-window commit,
and a commit whose offset moves it to the next UTC day), the Sunday
alignment, the default and capped window, and team-tracker's query shape.
Full suite passing.

## Notes

Commits come from `commit_metadata`, which the push indexer fills from the
last 500 commits of the pushed ref. A repository created with `auto_init`
and never pushed to has no indexed commits, so its initial commit does not
count; that matches how little GitHub credits a repository nobody pushed
to, and it is the indexer's scope, not this field's.
