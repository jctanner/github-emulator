# API-Client Frontend Migration

## Outcome

Completed on 2026-08-30. The React API-client frontend is the sole `/ui`
surface; `/ui-legacy`, the Jinja implementation, parity-only tooling, and the
broad `/admin` browser redirect have been retired.

## Goal

Replace the tightly coupled Jinja2 browser UI with a typed frontend that uses
the emulator's REST and GraphQL APIs, while retaining the current UI for
side-by-side Playwright comparison until functional and visual parity is
accepted.

## Target Architecture

```text
src/app/                    FastAPI backend and API contracts
src/frontend/               React and TypeScript source
src/frontend/dist/          generated, gitignored build output

/ui/...                     new frontend
/ui-legacy/...              retained Jinja2 frontend
/api/v3/...                 REST API
/graphql                    GraphQL API
```

The new frontend will use strict TypeScript, linting, formatting, component
tests, and an OpenAPI-generated client. Handwritten network calls should be
limited to APIs that cannot be represented by the generated client and
documented when needed.

The legacy admin UI will move from `/ui/_admin` to
`/ui-legacy/_admin`. The new admin surface will live at `/ui/_admin`.
Legacy `/admin` compatibility redirects must continue to resolve safely.

## Migration Rules

- Backend APIs, not templates or database models, are the frontend contract.
- Missing UI capabilities are added to REST or GraphQL before migrating the
  affected interaction.
- Frontend-specific endpoints may provide browser session/bootstrap behavior,
  but must not duplicate domain operations already exposed by an API.
- Jinja templates remain behaviorally frozen except for the legacy prefix,
  parity instrumentation, and critical fixes required to keep comparisons
  valid.
- Each migrated route needs API contract tests, component or interaction tests,
  and Playwright parity evidence.
- `/ui-legacy` is removed only after the complete route inventory is accepted.

## Milestones

### M0 — Contract and Route Inventory

- Inventory every Jinja page, form action, redirect, template context field,
  and browser-visible mutation.
- Map each requirement to an existing REST/GraphQL operation or record an API
  gap.
- Define browser authentication, CSRF, session expiry, and API error handling
  before selecting implementation details.
- Record deterministic fixtures and volatile fields that screenshot tests must
  normalize.

**Pause point:** review the route/API gap matrix and authentication design.

### M1 — Preserve the Legacy UI

- Remount Jinja routes, templates, assets, links, and form actions under
  `/ui-legacy`.
- Preserve `/ui/_admin` until the new shell is ready, then move its Jinja
  equivalent to `/ui-legacy/_admin`.
- Add route-contract tests proving legacy navigation cannot accidentally cross
  into the new frontend.
- Verify ordinary namespaces such as the seeded `admin` user remain
  unambiguous.

**Pause point:** verify the complete existing UI works under `/ui-legacy`.

### M2 — Frontend Foundation

- Create `src/frontend` with React, strict TypeScript, Vite, ESLint, formatting,
  Vitest, and Testing Library.
- Generate TypeScript API types/client code from FastAPI OpenAPI output in a
  reproducible target.
- Add shared routing, authentication state, error handling, page layout, and
  GitHub-compatible design tokens.
- Build the frontend in container and local development workflows and serve it
  under `/ui`.

**Pause point:** review build reproducibility, typed API usage, and the empty
application shell.

### M3 — Parity Harness

- Add a Playwright route manifest that opens equivalent `/ui` and `/ui-legacy`
  pages against the same deterministic seed.
- Compare screenshots, accessible names, text, links, controls, navigation,
  and mutations.
- Normalize timestamps, generated IDs, live job state, animation, and other
  volatile content.
- Store diff artifacts in ignored test-output directories.

**Pause point:** approve parity thresholds and baseline artifacts.

### M4 — Read-Only Repository Experience

- Migrate the global shell, owner/repository pages, code browsing, issue and
  pull-request lists, and detail views.
- Add missing read APIs discovered in M0 before consuming them.
- Preserve deep links and GitHub-compatible route shapes.

**Pause point:** compare the principal read-only workflows in both UIs.

### M5 — Repository Mutations

- Migrate issue, pull-request, comment, label, branch, file, and repository
  mutations.
- Validate permissions, error responses, event dispatch, redirects/navigation,
  and optimistic state against the API contracts.
- Ensure mutations do not bypass event-generation paths used by Actions and
  Fullsend.

**Pause point:** run seeded end-to-end issue-to-PR workflows through both UIs.

### M6 — Actions and Repository Settings

- Migrate workflow/run/job/log/runner views, including live log behavior.
- Migrate general, access, branch-protection, and GitHub Apps repository
  settings.
- Add API coverage for any settings still implemented only by Jinja handlers.

**Pause point:** validate Actions visibility, settings mutations, and runner
management parity.

### M7 — Administrative UI

- Implement `/ui/_admin` as an API client for users, organizations,
  repositories, imports, tokens, Apps/installations, and runners.
- Preserve machine-facing admin API paths and authorization behavior.
- Redirect legacy `/admin` browser URLs to the new admin surface.

**Pause point:** complete administrative parity and destructive-operation
review.

### M8 — Cutover and Legacy Retirement

- Require the frontend lint, typecheck, unit, backend contract, and Playwright
  suites in normal validation.
- Run the complete emulator regression suite, upstream-runner smoke test, and
  Breadboard rebuild/deployment validation.
- Resolve or explicitly accept every route-manifest difference.
- Remove `/ui-legacy`, Jinja routes/templates, and temporary compatibility
  helpers only after approval.
- Update architecture documentation and ownership boundaries.

**Pause point:** explicit approval before deleting the legacy implementation.

## Completion Criteria

- Every browser route and interaction is represented in the route/API matrix.
- The frontend compiles under strict TypeScript and passes lint and component
  tests.
- Browser business operations use versioned REST or GraphQL contracts.
- Playwright parity results are accepted for desktop and narrow viewports.
- Fullsend event flows and Actions live logs remain functional.
- The upstream Actions runner and Breadboard deployment smoke tests pass.
- Legacy deletion is a separate, reviewable change.

## Non-Goals

- Replacing FastAPI or changing Git/runner transport protocols.
- Making visual redesign a prerequisite for API separation.
- Sharing provider-specific frontend code with the GitLab emulator during this
  migration.
- Removing the legacy UI before explicit parity approval.
