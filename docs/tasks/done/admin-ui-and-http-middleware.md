# Task: Admin UI Namespace and HTTP Middleware

## Goal

Move browser administration to `/ui/_admin` and add request tracing and
baseline browser security headers without changing admin API paths.

## Acceptance Criteria

- [x] Admin pages and assets use `/ui/_admin`.
- [x] Legacy `/admin` GET URLs redirect while `/admin/api` remains an API path.
- [x] `/ui/admin` remains available for the `admin` user namespace.
- [x] Responses include security and GitHub request-ID headers.
- [x] Focused admin and middleware tests pass.

## Status

Complete

## Validation

- Focused admin and middleware suite: 29 passed.
- Complete suite: 368 passed.
- Live `/ui/_admin/login` returned 200 with request-ID and security headers.
- Legacy `/admin/login` redirected to `/ui/_admin/login`, while
  `/admin/api/apps` remained an API endpoint.
