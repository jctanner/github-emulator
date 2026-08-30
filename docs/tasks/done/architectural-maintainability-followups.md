# Task: Architectural Maintainability Follow-Ups

## Goal

Improve the GitHub emulator's internal boundaries, schema evolution, and
operational consistency without changing its GitHub-compatible behavior or
runner strategy.

## Context

The application is a FastAPI monolith with separate REST, GraphQL, service,
model, Git transport, admin, and Jinja2 web layers. The recent `src/` move made
the production-code boundary explicit, but several modules have grown large and
database upgrades still happen through startup-time conditional DDL.

The real `actions/runner` remains the preferred fidelity target. The
deterministic Python runner remains useful for controlled tests and Fullsend
development; this task does not replace either execution path.

## Proposed Work

1. Split `web/routes.py` and `admin/routes.py` into feature-oriented routers
   while preserving URLs, template context, authorization, and route ordering.
2. Move the browser-based administration surface from `/admin` to the reserved
   `/ui/_admin` namespace so all HTML interfaces live under `/ui`. Move its
   static assets with it, update generated links and redirects, and preserve
   temporary redirects from legacy `/admin` page URLs. Keep machine-facing
   administration APIs on their existing paths unless a separate API migration
   is explicitly designed and versioned. Do not use `/ui/admin`, because it
   conflicts with the seeded `admin` user's normal UI namespace.
3. Separate workflow parsing/materialization, event routing, scheduling, and
   upstream-runner protocol handling currently concentrated in
   `workflow_service.py` and the Actions API modules.
4. Replace startup-time `ALTER TABLE` compatibility logic with versioned
   Alembic revisions, including upgrade tests from representative old schemas.
5. Add request-ID and security-header middleware consistent with the GitLab
   emulator, with compatibility tests for API, UI, Git transport, and runner
   endpoints.
6. Define one transaction/retry boundary for SQLite writes and document the
   single-replica limitation instead of adding endpoint-specific lock handling.
7. Organize oversized test modules by capability and retain end-to-end contract
   tests for REST, GraphQL, Git, event dispatch, and both runner protocols.

## Acceptance Criteria

- [x] Each proposed area is split into a focused child task before implementation.
- [x] Public API paths, web routes, event payloads, and runner protocols remain
  backward compatible unless a separately documented compatibility fix requires
  a change.
- [x] Admin HTML pages and assets are served from `/ui/_admin`; legacy `/admin`
  browser URLs redirect safely, and ordinary `/ui/admin` user/repository routes
  remain unaffected.
- [x] Database upgrades use Alembic and are validated from at least one older
  persisted emulator schema.
- [x] Actions scheduling and runner-protocol boundaries are documented.
- [x] Focused regression tests pass for each child task; the complete regression
  suite and real-runner smoke test pass for the integrated result.
- [x] Breadboard deployment remains reproducible through its existing build and
  deployment targets.

## Non-Goals

- Extracting a shared GitHub/GitLab domain framework.
- Replacing Jinja2 with a client-side frontend framework.
- Reimplementing the upstream Actions runner in Python.
- Making the emulator horizontally scalable or production-grade.

## Status

Complete

## Notes

Prefer sharing narrowly scoped test utilities, Git storage helpers, middleware,
or template macros only after their contracts are explicit. GitHub and GitLab
identity, authorization, event, merge, and runner semantics should remain
provider-specific.

## Validation

- Complete suite: 368 passed.
- Breadboard rebuild: `make host-rebuild-github`.
- Live admin UI: `/ui/_admin/login` returned 200 and legacy
  `/admin/login` redirected to it.
- Live admin API: `/admin/api/apps` remained an authenticated API endpoint.
- Existing database upgraded to Alembic revision `0001_baseline`.
- Site-wide upstream Actions runner 2.317.0 remained connected and listening.
