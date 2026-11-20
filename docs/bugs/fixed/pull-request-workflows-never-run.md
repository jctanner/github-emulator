# Bug: `on: pull_request` workflows never run

Status: fixed 2026-11-20.

## Summary

On GitHub, one pull request activity (opened, labeled, synchronize, closed,
and the rest) runs both `pull_request_target` and `pull_request` workflows.
The emulator raised every pull request activity only as
`pull_request_target`, from REST, GraphQL `createPullRequest`, labels, issue
edits, auto-merge and pushes to a pull request's head. A workflow on
`pull_request` never ran.

Fullsend's dispatch shim moved from `pull_request_target` to `pull_request`
(GitHub blocks `pull_request_target` by default in public repositories from
2026-12-02), so on the emulator no pull request activity dispatched an agent.
The emulator also had no `refs/pull/N/merge`.

## Fix

Every activity raised as `pull_request_target` now also runs the
`pull_request` workflows, as GitHub does
(`app.services.pull_request_runs`, called from `dispatch_event`):

- The emulator writes `refs/pull/N/head` and `refs/pull/N/merge`: the head
  merged into the base branch's current tip, with the base and head as
  parents, authored by GitHub.
- A `pull_request` run reads its workflow files from that merge commit.
  `GITHUB_SHA` is the merge commit, `GITHUB_REF` is `refs/pull/N/merge`, the
  run's `head_branch` is the pull request's head branch, and
  `github.base_ref` and `github.head_ref` are the pull request's branches.
- A pull request that does not merge cleanly has no merge ref, and no
  `pull_request` workflow runs for it, as on GitHub.
- A fork's pull request (`head.repo.id` differs from the repository's): its
  `pull_request` run gets no secrets, a token whose writes are lowered to
  reads whatever the workflow declares, and no OIDC token. A same-repository
  pull request's run keeps the normal token and secrets. GraphQL's job-token
  scope check (`app.graphql.authz`) refuses a fork run's writes too, so they
  stay refused once job tokens can call GraphQL
  (`docs/bugs/open/job-tokens-cannot-use-graphql.md`).
- The activity's webhook is sent once, as before.

`pull_request_target` runs are unchanged.

## Known gaps

- For a `closed` activity the merge ref is recomputed against the base
  branch's tip. For a merged pull request GitHub reports the merge it made.
- Each pull request activity now writes a merge commit and may create a second
  set of runs, which adds to the single-writer load.

Tests: `tests/actions/test_pull_request_event.py`.
