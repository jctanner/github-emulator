# GitHub Emulator

A lightweight, self-contained emulator of the GitHub API designed for integration testing.

## Features

- **REST API** -- compatible subset of the GitHub REST API v3 (`/api/v3`, `/repos`, `/user`, `/orgs`, etc.)
- **GraphQL API** -- Strawberry-based implementation of common GitHub GraphQL queries
- **Git Smart HTTP** -- clone, fetch, and push over HTTP against bare repositories managed by the emulator
- **Admin UI** -- browser-based dashboard for managing users, tokens, organisations, and repositories
- **Webhooks** -- event delivery with recorded payloads
- **SQLite + aiosqlite** -- zero-dependency storage; no external database server required

## Quick Start

The fastest way to run the emulator is with Docker Compose:

```bash
make up
# or equivalently:
docker compose up -d
```

The server will be available at **http://localhost:8000**.

Default admin credentials: `admin` / `admin`.

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

The database URL is read from the `GITHUB_EMULATOR_DATABASE_URL` environment
variable. If unset, it defaults to `sqlite+aiosqlite:///data/github_emulator.db`.

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

## Makefile Targets

| Target    | Description                                      |
|-----------|--------------------------------------------------|
| `build`   | Build the Docker image                           |
| `up`      | Build and start the container                    |
| `down`    | Stop and remove containers and volumes           |
| `restart` | Rebuild and restart from scratch                 |
| `logs`    | Tail container logs                              |
| `test`    | Run pytest locally                               |
| `smoke`   | End-to-end smoke test against the running server |
| `clean`   | Remove containers, images, and build artifacts   |

## Project Structure

```
github_emulator/
  app/
    api/          # REST API route handlers
    admin/        # Admin UI (Jinja2 templates + static assets)
    git/          # Git Smart HTTP transport
    graphql/      # Strawberry GraphQL schema and types
    models/       # SQLAlchemy ORM models
    schemas/      # Pydantic request / response schemas
    services/     # Business-logic layer
    middleware/   # FastAPI middleware
    config.py     # Settings (env-driven via pydantic-settings)
    database.py   # Async engine, session, Base
    main.py       # Application entrypoint
  alembic/        # Database migration scripts
  tests/          # Pytest test suite
  Dockerfile
  docker-compose.yml
  Makefile
  pyproject.toml
```

## Important Note

This project is intended **for integration testing only**. It implements just
enough of the GitHub API surface to exercise client libraries, CI tooling, and
automation scripts in isolated environments. It is **not** a production-grade
GitHub replacement and should never be exposed to untrusted networks.
