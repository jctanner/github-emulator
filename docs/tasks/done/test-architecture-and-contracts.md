# Task: Test Architecture and Contracts

## Goal

Group large tests by capability and retain explicit REST, GraphQL, Git, event,
deterministic-runner, and upstream-runner contracts.

## Acceptance Criteria

- [x] Oversized tests are split without reducing assertions.
- [x] Route and runner protocol inventories prevent accidental surface loss.
- [x] Focused suites and the complete suite pass.
- [x] Real-runner and breadboard rebuild validation are recorded.

## Status

Complete

## Validation

- Actions capability suite: 50 passed.
- Route contract inventory passed.
- Complete suite: 368 passed.
- `make host-rebuild-github` completed and the upstream Actions runner remained
  connected and listening.
