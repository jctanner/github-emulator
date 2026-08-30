# Architecture Boundaries

The GitHub emulator is a single-replica integration-test service. FastAPI owns
the REST, GraphQL, Jinja2, Git transport, and Actions coordinator surfaces;
SQLite and bare repositories share one persistent data volume.

## Web and Administration

- `app.web.routes` is the compatibility facade for the `/ui` frontend.
- `app.web.settings_routes` owns repository Settings pages.
- `app.admin.routes` is the compatibility facade for `/ui/_admin`.
- `app.admin.apps_routes` owns GitHub App and authentication administration.
- `/admin` browser GETs are compatibility redirects. `/admin/api` remains a
  machine-facing administration namespace.

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
