# Frontend Cutover Validation

The React API-client frontend is the canonical `/ui` surface. The Jinja UI is
still mounted at `/ui-legacy` for review and has not been approved for removal.

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

## Remaining approval gate

Routes remain classified as `candidate` in the parity manifest. The current
suite proves both surfaces are reachable at the same route and fixture state;
it does not claim pixel or semantic equivalence. Baseline acceptance and legacy
deletion therefore remain explicit review steps. No Jinja routes, templates,
or `/ui-legacy` compatibility helpers should be removed before that approval.
