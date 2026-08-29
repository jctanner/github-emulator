# Task: Enterprise-scoped Real Actions Runner

## Goal

Run the upstream GitHub `actions/runner` binary as an enterprise-scoped worker
that can accept matching jobs from every emulator repository.

## Context

GitHub documents enterprise registration through
`POST /enterprises/{enterprise}/actions/runners/registration-token` followed by
`config.sh --url https://github.com/enterprises/{enterprise}`. The emulator's
real-runner path previously registered against one repository, while its
internal job claim was accidentally unfiltered.

## Acceptance Criteria

- [x] Enterprise registration/remove-token and runner-list APIs use GitHub-compatible paths.
- [x] Only a site administrator can manage the configured emulator enterprise runners.
- [x] Upstream `config.sh` can register against `/enterprises/breadboard`.
- [x] Enterprise runners claim matching jobs across repositories.
- [x] Repository real runners only claim jobs from their registered repository.
- [x] The admin page identifies the enterprise scope without exposing credentials.
- [x] Breadboard deploys the upstream runner binary for `fullsend-router` jobs.
- [x] A Fullsend target event routes successfully through the real runner into `.fullsend`.

## Status

Complete

## Notes

- 2026-08-29: Contract follows GitHub's documented enterprise self-hosted runner
  API and `config.sh --url .../enterprises/{enterprise}` shape. Breadboard uses
  the single configured enterprise slug `breadboard`.
- 2026-08-29: The real runner contract preserves event payloads, step IDs,
  environments, runtime expressions, step conditions, and job-scoped API
  authorization. Job OAuth credentials are signed JWTs because upstream runner
  2.317.0 parses the token before starting its worker process.
- 2026-08-29: Live issue edit produced target run `1034`/job `1682` on
  `breadboard-enterprise-router`; both router steps succeeded and dispatched
  central `.fullsend` run `1035`.
- 2026-08-29: Focused emulator tests: 12 passed. Fullsend seed contract tests:
  5 passed.
