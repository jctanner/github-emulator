# Architecture Boundaries

The GitHub emulator is a single-replica integration-test service. FastAPI owns
REST, GraphQL, Git transport, Actions coordination, browser sessions, and the
static production frontend host. React owns the canonical browser UI; SQLite
and bare repositories share one persistent data volume.

## Web and Administration

- `src/frontend` is the canonical `/ui` and `/ui/_admin` React application.
- Versioned REST/GraphQL schemas are its business-operation boundary; generated
  OpenAPI types live in `src/frontend/src/api/schema.d.ts`.
- `app.frontend` serves the production bundle and SPA history fallback.
- `app.api.browser_session` supplies same-origin cookie authentication and
  CSRF tokens; admin APIs authorize the same user with `site_admin`.
- `app.web.routes`, `app.web.settings_routes`, `app.admin.routes`, and
  `app.admin.apps_routes` are frozen Jinja parity implementations mounted only
  under `/ui-legacy` until explicit retirement approval.
- `/admin` browser GETs are compatibility redirects. `/admin/api` remains a
  machine-facing administration namespace.

Local development uses `Procfile.dev`: Honcho runs FastAPI on port 8000 and
Vite on port 5173, while Vite proxies backend-owned paths. Production builds
the frontend in the container image and serves it from FastAPI under `/ui`.

## Actions

- `workflow_expressions` safely evaluates the supported expression subset.
- `workflow_triggers` matches repository events to workflow declarations.
- `workflow_service` detects/materializes workflows and dispatches events.
- `workflow_scheduler` promotes jobs and finalizes or cancels runs.
- `actions_runner_protocol` serializes the upstream runner protocol.
- `actions_distributed_task` owns its HTTP and session lifecycle.

The upstream `actions/runner` is the fidelity target. The Python runner remains
a deterministic integration and Fullsend development path.

## Persistence

Alembic upgrades the database before startup. SQLite uses WAL mode, a bounded
busy timeout, and one shared retry helper for operations that can replay their
state after rollback. Other lock failures become a retryable HTTP 503.

The deployment must remain at one application replica. SQLite and local bare
repositories do not provide a safe horizontally scalable storage boundary.
