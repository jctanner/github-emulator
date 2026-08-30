# Task: SQLite Write Policy

## Goal

Centralize bounded retries for transient SQLite writer contention and document
the emulator's single-replica persistence boundary.

## Acceptance Criteria

- [x] Write retries use one shared policy and configuration source.
- [x] Endpoints do not implement private lock-detection loops.
- [x] Exhausted contention returns the existing retryable 503 response.
- [x] Lock-handling tests cover success-after-retry and exhaustion.
- [x] Deployment documentation states the single-replica limitation.

## Status

Complete

## Validation

- Migration and contention suite: 7 passed.
- Complete suite: 368 passed.
- The persistence boundary is documented in the README and architecture notes.
