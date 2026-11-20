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
  `github.base_ref` and `github.head_ref` are the pull request's branches,
  in the server-side context (job `if:`, rendered steps, jobs with `needs`)
  as well as the runner's.
- A pull request that does not merge cleanly has no merge ref, and no
  `pull_request` workflow runs for it, as on GitHub.
- A same-repository head must be a branch of the repository at its tip (or,
  once the branch is deleted, the commit an earlier activity recorded as
  `refs/pull/N/head`). REST `create_pull` also accepts any revision or a
  supplied `head_sha` as a same-repository head, which GitHub refuses; such a
  pull request (one naming another pull request's `refs/pull/M/head`, say)
  runs no `pull_request` workflow.
- A fork's pull request (`head.repo.id` differs from the repository's): its
  `pull_request` run gets no secrets, a token whose writes are lowered to
  reads whatever the workflow declares, and no OIDC token on either runner
  path (the job message has no `ACTIONS_ID_TOKEN_REQUEST_*`, and the
  emulator runner's claim says `id_token: false`). A same-repository pull
  request's run keeps the normal token and secrets. GraphQL's job-token
  scope check (`app.graphql.authz`) refuses a fork run's writes too, so they
  stay refused once job tokens can call GraphQL
  (`docs/bugs/open/job-tokens-cannot-use-graphql.md`).
- The activity's webhook is sent once, as before.

`pull_request_target` runs are unchanged.

A job token, for any event, now sees what GitHub's `GITHUB_TOKEN` sees,
not what the run's actor sees:

- No repository other than its run's that is not public: 404 through the
  API, git, raw and release-download routes.
- User-scoped endpoints (`/user`, `/user/...`, `/notifications`) answer 403
  "Resource not accessible by integration".
- Listings and search hold its own repository plus public ones:
  `/repositories`, `/users/{user}/repos`, `/orgs/{org}/repos`,
  `/users/{user}/starred`, `/users/{user}/events`, an organization
  variable's selected repositories, and `/search/repositories`, `issues`,
  `code` and `commits` (`app.services.job_token_scope`).
- GraphQL's visibility gate (`app.graphql.authz.visible`), which
  `repository`, `node`, `search` and organization repository connections go
  through, applies the same rule.

`/installation/repositories` already listed only the run's repository.

## Known gaps

- For a `closed` activity the merge ref is recomputed against the base
  branch's tip. For a merged pull request GitHub reports the merge it made.
- Each pull request activity now writes a merge commit and may create a second
  set of runs, which adds to the single-writer load.
- REST `create_pull` still accepts a non-branch same-repository head (GitHub
  answers 422); only its `pull_request` runs are withheld.
- For other credentials, `/search/issues`, `/search/code` and
  `/search/commits` still search every repository, private ones included.
  A job token's GraphQL `viewer` is the run's actor, not
  `github-actions[bot]`. Both predate this change.

Tests: `tests/actions/test_pull_request_event.py`.
