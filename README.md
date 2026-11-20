# GitHub Emulator

GitHub Emulator is a self-contained copy of the GitHub API that you run for
integration tests. It serves REST and GraphQL, git over HTTP and SSH, a web UI
and Actions runners. Point client libraries, `gh` and automation scripts at it
instead of real GitHub.

> **Integration testing only.** Run a single replica, and never expose it to
> untrusted networks. See [architecture boundaries](docs/notes/architecture-boundaries.md).

## Quickstart (container)

**Prerequisites**

- Docker with Compose, or Podman (add `CONTAINER_ENGINE=podman` to each `make` command).
- Host ports 80, 443, 8000 and 2222 free.
- `make`, `curl`, `python3` and `git` on your host, for the steps below.

**Steps**

1. Build and start the stack:

   ```console
   $ make up
   ...
   Server is up at http://localhost:8000
   ```

   `make up` waits up to ten seconds for the API, then prints
   `Server is up at http://localhost:8000` either way; step 2 is the real check.
   If it stops with `bind: address already in use`, a port is taken:
   see [Troubleshooting](docs/guides/development.md#troubleshooting), or use the
   [no-container quickstart](#quickstart-no-container).

2. Make your first API call with the seeded admin token:

   ```console
   $ curl -s http://localhost:8000/api/v3/user -H "Authorization: token ghp_admin_default_token" | python3 -m json.tool | head -4
   {
       "login": "admin",
       "id": 1,
       "node_id": "VXNlcjox",
   ```

3. Run the end-to-end smoke test:

   ```console
   $ make smoke
   ...
   === Verify clone ===
   Cloning into '/tmp/smoke-verify'...
   # Smoke Test
   PASS: File content verified

   === Smoke test complete ===
   ```

For more about the seeded token, see the
[default admin token guide](docs/guides/default-admin-pat.md).

## Quickstart (no container)

> Use this route if you have no container engine, or ports 80 and 443 are taken.
>
> ```console
> $ uv sync --frozen --extra dev
> Installed 56 packages in 133ms
> $ uv run uvicorn app.main:app
> INFO github_emulator: GitHub Emulator started at http://localhost:8000
> INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
> ```
>
> Then run the same `curl` as above. To build the web UI and run the tests,
> follow the [development guide](docs/guides/development.md).

## Endpoints and default credentials

| What | Where |
|---|---|
| REST API | `http://localhost:8000/api/v3` |
| GraphQL | `http://localhost:8000/api/graphql` |
| Web UI | `http://localhost:8000/ui/` |
| Admin UI | `http://localhost:8000/ui/_admin/` |
| Git over HTTP | `http://localhost:8000/<owner>/<repo>.git` |
| Git over SSH | port `2222` |
| Admin login | `admin` / `admin` |
| Admin token | `ghp_admin_default_token` |

## Features

- **REST API**: a compatible subset of GitHub REST v3: repositories, issues, pull requests, labels, milestones, comments, reviews, reactions, branches, commits, contents, releases, deploy keys, statuses, check runs, search, stars, notifications, gists and more.
- **GraphQL API**: common queries and mutations (Strawberry).
- **Git**: clone, fetch and push over Smart HTTP/HTTPS and over SSH (port 2222).
- **Web UI** (`/ui/`): repositories, files, commits, issues, pull requests, Actions and settings.
- **Admin UI** (`/ui/_admin/`): users, tokens, organisations, repositories, GitHub Apps and installations, runners and imports.
- **GitHub import**: clone one repo by URL, or every repo of a GitHub user or org.
- **Webhooks**: event delivery with recorded payloads.
- **`gh` CLI**: works as a `GH_HOST` target.
- **TLS via Caddy**: HTTPS with a local CA for realistic `gh` and git tests.
- **SQLite**: no external database server.

## Documentation

- [Development guide](docs/guides/development.md): run from source, build the web UI, run the tests.
- [API usage](docs/guides/api-usage.md): tokens, repositories, issues, GraphQL, Apps, git, `gh` over TLS, Vagrant.
- [Actions runners](docs/guides/actions-runners.md): run workflow jobs and view them in the web UI.
- [Configuration](docs/guides/configuration.md): every `GITHUB_EMULATOR_` setting.
- [Default admin token](docs/guides/default-admin-pat.md): the seeded token and how to change it.
- [CONTRIBUTING](CONTRIBUTING.md): checks, policies and how to submit a change.
- [Architecture boundaries](docs/notes/architecture-boundaries.md): modules, runners, migrations and write contention.
- [Work ledger](PLAN.md): tasks, bugs and decisions.

## Project structure

```text
src/
  app/
    api/            # REST API route handlers
    git/            # Git Smart HTTP and SSH transport
    graphql/        # Strawberry GraphQL schema, queries, mutations, types
    middleware/     # FastAPI middleware (auth, rate limiting, ETag, error handling)
    models/         # SQLAlchemy ORM models
    schemas/        # Pydantic request/response schemas
    services/       # Business logic (import, webhooks, search, workflows, ...)
    config.py       # Settings (env-driven via pydantic-settings)
    database.py     # Async engine, session factory, Base
    frontend.py     # Serves the built web UI
    main.py         # Application entrypoint
  frontend/         # React web UI and admin UI (Vite)
  runners/
    emulator/       # Deterministic Python Actions runner
    upstream/       # Official actions/runner container wrapper
alembic/            # Database migration scripts
docs/               # Guides, notes and the work ledger
tests/              # Pytest test suite
scripts/            # Integration and helper scripts
Dockerfile
docker-compose.yml
Caddyfile
supervisord.conf    # Runs Caddy + Uvicorn inside the container
Vagrantfile         # Two-VM dev environment (server + client)
Makefile
pyproject.toml
```
