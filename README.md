# GitHub Emulator

A lightweight, self-contained emulator of the GitHub API designed for integration testing.
Run it locally or in CI to exercise client libraries, `gh` CLI workflows, and automation
scripts without touching real GitHub.

## Features

- **REST API** -- compatible subset of GitHub REST API v3 (repositories, issues, pull requests, labels, milestones, comments, reviews, reactions, branches, commits, contents, releases, deploy keys, commit statuses, check runs, search, starring, notifications, gists, and more)
- **GraphQL API** -- Strawberry-based implementation of common GitHub GraphQL queries and mutations
- **Git Smart HTTP** -- clone, fetch, and push over HTTP/HTTPS against bare repositories
- **Git SSH Transport** -- clone and push over SSH (port 2222 by default)
- **Web UI** (`/ui/`) -- typed React API client for repositories, files, commits, issues, pull requests, Actions, and settings
- **Admin Panel** (`/ui/_admin/`) -- API-client administration for users, tokens, organisations, repositories, GitHub Apps/installations, runners, and imports
- **GitHub Import** -- clone a single repo by URL or bulk-import all repos from a GitHub user/org via the admin panel
- **Webhooks** -- event delivery with recorded payloads
- **`gh` CLI Compatible** -- works as a `GH_HOST` target for the GitHub CLI
- **TLS via Caddy** -- automatic HTTPS with a local CA for realistic `gh`/git testing
- **SQLite + aiosqlite** -- zero-dependency storage; no external database server required

## Quick Start

### Docker Compose (recommended)

```bash
make up
# or:
docker compose up -d
```

The server will be available at:

| Endpoint | URL |
|---|---|
| REST API | `http://localhost:8000/api/v3` |
| Web UI | `http://localhost:8000/ui/` |
| Admin Panel | `http://localhost:8000/ui/_admin/` |
| GraphQL | `http://localhost:8000/api/graphql` |

Default admin credentials: `admin` / `admin`. Fresh instances also seed a
default admin personal access token, `ghp_admin_default_token`, so API clients
can authenticate immediately.

### Vagrant (two-VM setup with TLS)

For full `gh` CLI integration testing with TLS, a Vagrantfile provisions a
**server** VM (Debian 12 + Docker, static IP `192.168.123.10`) and a **client**
VM for running tests:

```bash
# Add the hostname to /etc/hosts
echo "192.168.123.10  ghemu.local" | sudo tee -a /etc/hosts

# Boot both VMs, sync code, build, and start
make vm-deploy

# The server is now reachable at https://ghemu.local
```

## Development Setup

```bash
# Create a virtual environment and install dependencies
uv venv
uv pip install -e ".[dev]"

# Run the test suite
uv run pytest tests/ -v

# Start the server locally (without Docker)
uv run uvicorn app.main:app --reload
```

For coordinated API and frontend development, install the frontend once and
run the Honcho process file:

```bash
make frontend-install
make dev
```

Honcho starts the reloadable FastAPI backend on port 8000 and Vite on port
5173. Open `http://127.0.0.1:5173/ui/`; Vite proxies API, avatar, admin API,
and GraphQL requests to FastAPI. The production image builds the same React
source and serves its static bundle directly from FastAPI.

## Configuration

All settings are driven by environment variables with the `GITHUB_EMULATOR_` prefix:

| Variable | Default | Description |
|---|---|---|
| `GITHUB_EMULATOR_BASE_URL` | `http://localhost:8000` | Base URL used in API response URLs |
| `GITHUB_EMULATOR_DATA_DIR` | `./data` | Directory for bare git repos and the SQLite DB |
| `GITHUB_EMULATOR_DATABASE_URL` | `sqlite+aiosqlite:///{DATA_DIR}/github_emulator.db` | SQLAlchemy database URL |
| `GITHUB_EMULATOR_SECRET_KEY` | `change-me-in-production` | Secret for JWT/session signing |
| `GITHUB_EMULATOR_SEED_DATA` | `true` | Seed default admin user and PAT at startup |
| `GITHUB_EMULATOR_ADMIN_USERNAME` | `admin` | Admin user created on first startup |
| `GITHUB_EMULATOR_ADMIN_PASSWORD` | `admin` | Admin user password |
| `GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN` | `ghp_admin_default_token` | Default admin PAT seeded at startup |
| `GITHUB_EMULATOR_HOSTNAME` | `ghemu.local` | Hostname for Caddy TLS certificate |
| `GITHUB_EMULATOR_APP_JWT_PERMISSIVE` | `true` | Skip App JWT signature verification; set `false` for strict verification |
| `GITHUB_EMULATOR_SSH_ENABLED` | `true` | Enable/disable the SSH transport |
| `GITHUB_EMULATOR_SSH_PORT` | `2222` | SSH server listen port |

The Compose stack maps the HTTP API port from `${PORT:-8000}`. For example,
`make CONTAINER_ENGINE=podman PORT=9000 up` exposes the service on port 9000
while the container continues listening on port 8000.

## Database Migrations (Alembic)

The project uses Alembic with async SQLAlchemy for schema migrations.

```bash
# Generate a new migration after changing models
uv run alembic revision --autogenerate -m "describe the change"

# Apply all pending migrations
uv run alembic upgrade head

# Downgrade one revision
uv run alembic downgrade -1
```

## API Usage Examples

### Create a personal access token

Fresh instances seed a default admin token:

```bash
curl -s http://localhost:8000/api/v3/user \
  -H "Authorization: token ghp_admin_default_token" \
  | python3 -m json.tool
```

Override it with `GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN` when starting the server.

To create an additional token:

```bash
curl -s -X POST http://localhost:8000/admin/tokens \
  -H "Content-Type: application/json" \
  -d '{"login":"admin","name":"my-token","scopes":["repo","user"]}' \
  | python3 -m json.tool
```

### Create a repository

```bash
TOKEN="<token-from-above>"

curl -s -X POST http://localhost:8000/user/repos \
  -H "Authorization: token $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-repo","description":"Test repo"}' \
  | python3 -m json.tool
```

### Use GitHub App compatibility endpoints

The resettable admin API exposes authenticated JSON setup endpoints matching
the local GitHub App integration surface:

```bash
curl -s -X POST http://localhost:8000/admin/api/apps \
  -H "Authorization: token $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"my-app","owner":"admin"}' | python3 -m json.tool

# App JWT-authenticated metadata and installations use the GitHub-style API.
curl -s http://localhost:8000/api/v3/app \
  -H "Authorization: Bearer $APP_JWT" | python3 -m json.tool
curl -s http://localhost:8000/api/v3/src/app/installations \
  -H "Authorization: Bearer $APP_JWT" | python3 -m json.tool
```

Client IDs are persisted per App. Existing SQLite databases are backfilled on
startup; private-key retrieval and rotation require the admin token.

### Clone and push

```bash
git clone http://localhost:8000/admin/my-repo.git /tmp/my-repo
cd /tmp/my-repo
echo "# Hello" > README.md
git add README.md && git commit -m "initial commit"
git push http://admin:$TOKEN@localhost:8000/admin/my-repo.git main
```

### Create an issue

```bash
curl -s -X POST http://localhost:8000/repos/admin/my-repo/issues \
  -H "Authorization: token $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Bug report","body":"Something is broken"}' \
  | python3 -m json.tool
```

### Use with `gh` CLI

The emulator uses Caddy to generate a self-signed TLS certificate. By default
`gh` and `git` will reject it. You need to tell both tools to skip certificate
verification:

```bash
# Skip TLS verification for gh
export GH_HOST=ghemu.local
export GH_INSECURE=1

# Skip TLS verification for git
git config --global http.sslVerify false

# Authenticate
gh auth login --hostname ghemu.local --with-token <<< "$TOKEN"

# Use normally
gh repo list
gh issue create --repo admin/my-repo --title "Test" --body "Hello"
```

> **Note:** If you prefer not to disable TLS verification globally, you can
> extract Caddy's root CA certificate from the container and add it to your
> system trust store instead. See the Caddy documentation for details.

### View GitHub Actions jobs in the Web UI

The repository Actions UI is available at:

```text
http://localhost:8000/ui/<owner>/<repo>/actions
```

The Docker Compose stack includes an `actions-runner` service. Bootstrap its
token and default repository, then start the runner:

```bash
make up
make actions-runner-env
docker compose up -d actions-runner
```

By default the runner watches `admin/test-repo`. Override it with:

```bash
RUNNER_REPO=admin/my-repo make actions-runner-env
docker compose up -d actions-runner
```

The deterministic Python runner also supports an emulator-wide scope for
administrator-managed shared workers:

```bash
RUNNER_SCOPE=site RUNNER_NAME=shared-runner \
RUNNER_LABELS=self-hosted,linux,shared docker compose up -d actions-runner
```

Site-wide runners register through the emulator-specific authenticated
`POST /api/v3/admin/actions/runners/register` endpoint and poll matching jobs
across every repository. Repository-scoped runner tokens cannot use this poll
path, and site-wide tokens cannot use repository-scoped polling.

The project roadmap prefers real `actions/runner` compatibility for maximum
fidelity, but the bundled Python runner is kept as the deterministic fallback.
It executes local shell `run:` steps that the emulator stores in the job payload
and uploads the captured logs.

An opt-in compose profile builds the upstream `actions/runner` binary for the
compatibility spike:

```bash
make up
make actions-runner-env
make actions-real-runner
# equivalent:
docker compose --profile real-runner up --build actions-real-runner
```

This real-runner path is the intended compatibility target. Repository- and
enterprise-scoped registration, session polling, timeline updates, log upload,
and completion have been validated against the emulator. The Python runner
remains the deterministic fallback and currently powers the tool-enriched
Fullsend worker image used by Breadboard.

Desktop Playwright validation can be run against the compose-served UI after a
workflow run exists:

```bash
python -m pip install playwright
python -m playwright install chromium
make actions-ui-smoke
```

## Makefile Targets

### Docker (local)

| Target | Description |
|---|---|
| `build` | Build the Docker image |
| `up` | Build and start the container |
| `down` | Stop and remove containers and volumes |
| `restart` | Rebuild and restart from scratch |
| `logs` | Tail container logs |
| `test` | Run pytest locally |
| `smoke` | End-to-end smoke test against the running server |
| `actions-runner-env` | Create `.env` values for the compose Actions runner |
| `actions-real-runner` | Start the opt-in real `actions/runner` compose profile |
| `actions-ui-smoke` | Run desktop Playwright smoke test against Actions UI |
| `clean` | Remove containers, images, and build artifacts |

### Vagrant

| Target | Description |
|---|---|
| `vm-up` | Boot the server and client VMs |
| `vm-deploy` | Sync, build, and start containers in the server VM |
| `vm-sync` | Rsync the codebase into the server VM |
| `vm-build` | Build the container image inside the server VM |
| `vm-start` | Start containers inside the server VM |
| `vm-stop` | Stop containers inside the server VM |
| `vm-logs` | Tail container logs inside the server VM |
| `vm-destroy` | Destroy all VMs |
| `vm-ssh` | SSH into the server VM |
| `vm-client-ssh` | SSH into the client VM |
| `vm-test` | Run `gh` CLI integration tests from the client VM |
| `vm-git-test` | Run git CLI integration tests from the client VM |
| `vm-gh` | Quick `gh repo list` from the client VM |

## Project Structure

```
src/
  app/
    api/            # REST API route handlers
    git/            # Git Smart HTTP and SSH transport
    graphql/        # Strawberry GraphQL schema, queries, mutations, types
    middleware/     # FastAPI middleware (auth, rate limiting, ETag, error handling)
    models/         # SQLAlchemy ORM models
    schemas/        # Pydantic request/response schemas
    services/       # Business-logic layer (import, webhooks, search, etc.)
    config.py       # Settings (env-driven via pydantic-settings)
    database.py     # Async engine, session factory, Base
    main.py         # Application entrypoint
  runners/
    emulator/       # Deterministic Python Actions runner
    upstream/       # Official actions/runner container wrapper
alembic/          # Database migration scripts
tests/            # Pytest test suite
scripts/          # Integration test scripts for gh/git CLI
Dockerfile
docker-compose.yml
Caddyfile
supervisord.conf  # Runs Caddy + Uvicorn inside the container
Vagrantfile       # Two-VM dev environment (server + client)
Makefile
pyproject.toml
```

## Important Note

This project is intended **for integration testing only**. It implements enough
of the GitHub API surface to exercise client libraries, CI tooling, and
automation scripts in isolated environments. It is **not** a production-grade
GitHub replacement and should never be exposed to untrusted networks.

The persisted SQLite database and bare Git repositories are designed for a
single emulator replica. Do not scale the application horizontally against the
same data volume. See `docs/notes/architecture-boundaries.md` for module,
runner, migration, and write-contention boundaries.
