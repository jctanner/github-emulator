# Task: Route and Actions Modularization

## Goal

Introduce feature-oriented web/admin routers and explicit workflow evaluation,
event-routing, scheduling, and runner-protocol module boundaries while
preserving every external route and payload.

## Acceptance Criteria

- [x] Web and admin routes are composed from feature routers.
- [x] Workflow expression, trigger, materialization, event, and scheduling
  responsibilities have named module boundaries.
- [x] Upstream runner protocol serialization is separated from HTTP handlers.
- [x] Existing imports remain compatible through documented facade exports.
- [x] Route inventory and Actions regression tests pass.

## Status

Complete

## Validation

- Web/admin focused suite: 47 passed.
- Workflow and Actions boundary suites: 58 passed.
- Actions capability suite: 50 passed.
- Complete suite: 368 passed.
