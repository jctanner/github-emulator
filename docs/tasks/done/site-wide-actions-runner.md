# Task: Site-wide Actions Runner

## Goal

Allow one administrator-managed Actions runner to accept matching jobs from
every repository in the emulator.

## Context

The current custom runner registration and polling paths are repository-scoped.
Fullsend onboarding therefore leaves each target repository's reusable-workflow
router queued unless another repository-specific worker is deployed.

## Acceptance Criteria

- [x] Only a site administrator can register a site-wide runner.
- [x] A site-wide runner can poll and claim matching jobs from any repository.
- [x] Repository runners remain restricted to their registered repository.
- [x] Job payloads identify their source repository for callbacks and runtime variables.
- [x] Re-registering the named site-wide worker replaces its credentials instead of accumulating another row.
- [x] Admin runner visibility identifies the site-wide scope.
- [x] Focused API and runner contract tests pass.

## Status

Done

## Notes

- 2026-08-28: Site-wide routing will use `self-hosted`, `linux`, and
  `fullsend-router`. Agent-capable jobs retain `fullsend`, preventing the
  minimal shared worker from accepting centralized agent execution.
- 2026-08-28: Added admin-only site-wide registration with named replacement,
  global label-matched polling, repository scope enforcement, source-repository
  job payloads, and dynamic worker callbacks. Fifteen focused emulator tests
  and five Fullsend seed contract tests passed. Compose forwards configurable
  runner scope, name, and labels while preserving repository-scoped defaults.
- 2026-08-28: Deployed the singleton `breadboard-site-router` using the minimal
  runner image and Kubernetes `Recreate` strategy. Target run 1020/job 1668 ran
  on the shared worker and dispatched central Triage run 1021, which completed
  successfully and updated issue `admin/ansible-agent-harness#1` as
  `fullsend-triage[bot]`.
