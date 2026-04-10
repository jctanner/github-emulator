# GitHub Emulator

A lightweight, self-contained emulator of the GitHub API designed for integration testing.
Run it locally or in CI to exercise client libraries, `gh` CLI workflows, and automation
scripts without touching real GitHub.

## Features

- **REST API** -- compatible subset of GitHub REST API v3 (repositories, issues, pull requests, labels, milestones, comments, reviews, reactions, branches, commits, contents, releases, deploy keys, commit statuses, check runs, search, starring, notifications, gists, and more)
- **GraphQL API** -- Strawberry-based implementation of common GitHub GraphQL queries and mutations
- **Git Smart HTTP** -- clone, fetch, and push over HTTP/HTTPS against bare repositories
- **Git SSH Transport** -- clone and push over SSH (port 2222 by default)
- **Web UI** (`/ui/`) -- browse repositories, files, commits, issues, and pull requests in a GitHub-like interface
- **Admin Panel** (`/admin/`) -- manage users, tokens, organisations, repositories, and import repos from real GitHub
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
| Admin Panel | `http://localhost:8000/admin/` |
| GraphQL | `http://localhost:8000/api/graphql` |

Default admin credentials: `admin` / `admin`.

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

## Configuration

All settings are driven by environment variables with the `GITHUB_EMULATOR_` prefix:

| Variable | Default | Description |
|---|---|---|
| `GITHUB_EMULATOR_BASE_URL` | `http://localhost:8000` | Base URL used in API response URLs |
| `GITHUB_EMULATOR_DATA_DIR` | `./data` | Directory for bare git repos and the SQLite DB |
| `GITHUB_EMULATOR_DATABASE_URL` | `sqlite+aiosqlite:///{DATA_DIR}/github_emulator.db` | SQLAlchemy database URL |
| `GITHUB_EMULATOR_SECRET_KEY` | `change-me-in-production` | Secret for JWT/session signing |
| `GITHUB_EMULATOR_ADMIN_USERNAME` | `admin` | Admin user created on first startup |
| `GITHUB_EMULATOR_ADMIN_PASSWORD` | `admin` | Admin user password |
| `GITHUB_EMULATOR_HOSTNAME` | `ghemu.local` | Hostname for Caddy TLS certificate |
| `GITHUB_EMULATOR_SSH_ENABLED` | `true` | Enable/disable the SSH transport |
| `GITHUB_EMULATOR_SSH_PORT` | `2222` | SSH server listen port |

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

```bash
# Point gh at the emulator
export GH_HOST=ghemu.local

# Authenticate
gh auth login --hostname ghemu.local --with-token <<< "$TOKEN"

# Use normally
gh repo list
gh issue create --repo admin/my-repo --title "Test" --body "Hello"
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
app/
  api/            # REST API route handlers
  admin/          # Admin panel (Jinja2 templates, static assets, routes)
  git/            # Git Smart HTTP and SSH transport
  graphql/        # Strawberry GraphQL schema, queries, mutations, types
  middleware/     # FastAPI middleware (auth, rate limiting, ETag, error handling)
  models/         # SQLAlchemy ORM models
  schemas/        # Pydantic request/response schemas
  services/       # Business-logic layer (import, webhooks, search, etc.)
  web/            # Web UI (Jinja2 templates with Primer CSS)
  config.py       # Settings (env-driven via pydantic-settings)
  database.py     # Async engine, session factory, Base
  main.py         # Application entrypoint
alembic/          # Database migration scripts
tests/            # Pytest test suite (219 tests)
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
