# Frontend Cutover Validation

The React API-client frontend is the canonical and only browser UI under
`/ui`. The Jinja UI and `/ui-legacy` parity mount were retired after explicit
approval on 2026-08-30.

## Automated evidence

- Strict TypeScript, ESLint, Prettier, generated OpenAPI types, Vitest, and the
  production Vite build pass through `make frontend-check` and
  `make frontend-build`.
- The complete backend regression suite passes.
- The live Breadboard image builds and rolls out with `make
  host-rebuild-github`.
- The Playwright route manifest passes in desktop and narrow viewports against
  authenticated `/ui` and `/ui-legacy` sessions. It discovers the latest pull
  request and workflow run instead of assuming database IDs.
- The upstream `actions/runner` 2.317.0 smoke path registers, receives a queued
  job, executes `echo real-runner-smoke`, uploads logs/timeline data, and
  completes the run successfully.

The upstream-runner smoke found two migration regressions that now have tests:
the `/admin/{repo}/_apis` protocol was shadowed by the legacy `/admin` browser
redirect, and local runner service URLs exposed an unreachable `:8000` port
instead of the port-80 compatibility proxy.

## Retirement decision

The final candidate differences were accepted for cutover. The parity harness,
Jinja routes and templates, `/ui-legacy`, and the broad `/admin` browser
compatibility redirect were removed. The latter is necessary because `admin`
is also a valid repository owner and Git Smart HTTP must be able to serve
`/admin/<repository>.git` without interception.
