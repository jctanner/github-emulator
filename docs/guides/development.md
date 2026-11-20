# Development Guide

This guide gets the emulator running from source and the test suite passing.
Start with step 1. If you only want a running server, the
[README quickstart](../../README.md#quickstart-container) is faster.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/)
- Python 3.11 or later (`requires-python = ">=3.11"`)
- Node 22 with npm (only for the web UI)
- Optional: Docker or Podman, for the container stack

## Steps

Run every command from the repository root.

1. Create the virtual environment and install the locked dependencies:

   ```console
   $ uv sync --frozen --extra dev
   Installed 56 packages in 133ms
   ```

2. Run the tests:

   ```console
   $ uv run pytest tests/ -q
   1373 passed, 4680 warnings in 581.79s (0:09:41)
   ```

   The full suite takes about ten minutes. Your counts will change as tests are added.

3. Build the web UI. Without this build, `GET /ui/` returns `Internal Server Error`:

   ```console
   $ make frontend-install
   $ npm --prefix src/frontend run build
   ✓ built in 724ms
   ```

4. Start the server:

   ```console
   $ uv run uvicorn app.main:app --reload
   ```

   Look for these startup lines:

   ```text
   INFO github_emulator: GitHub Emulator started at http://localhost:8000
   INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
   ```

   These lines were captured without `--reload`. With `--reload`, uvicorn also prints its reloader lines.

5. Check the server from a second shell with the seeded admin token:

   ```console
   $ curl -s http://localhost:8000/api/v3/user -H "Authorization: token ghp_admin_default_token" | python3 -m json.tool | head -4
   {
       "login": "admin",
       "id": 1,
       "node_id": "VXNlcjox",
   ```

6. Optional: work on the API and the web UI together, with Vite hot reload
   (after `make frontend-install` from step 3):

   ```bash
   make dev
   ```

   Not run for this guide: it starts two long-running dev servers.

   Honcho, a process manager from the dev dependencies, reads `Procfile.dev`
   and starts the reloadable FastAPI backend on port 8000 and Vite on port
   5173. Open `http://127.0.0.1:5173/ui/`. Vite proxies API, avatar, admin API
   and GraphQL requests to FastAPI. The production image builds the same React
   source and serves its static bundle directly from FastAPI.

When this works, see [CONTRIBUTING](../../CONTRIBUTING.md) for the checks a
change must pass.

## Database migrations (Alembic)

Not run for this guide: the commands change the local database schema.

The project uses Alembic with async SQLAlchemy for schema migrations.

```bash
# Generate a new migration after changing models
uv run alembic revision --autogenerate -m "describe the change"

# Apply all pending migrations
uv run alembic upgrade head

# Downgrade one revision
uv run alembic downgrade -1
```

## Makefile targets

Run `make help` for the full list. Add `CONTAINER_ENGINE=podman` to use Podman.

### Container (local)

| Target | Description |
|---|---|
| `build` | Build the container image |
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

### Development and web UI

| Target | Description |
|---|---|
| `dev` | Start the reloadable API and Vite frontend with Honcho |
| `frontend-install` | Install the locked frontend dependencies (`npm ci`) |
| `frontend-api` | Regenerate the typed frontend API contract from FastAPI OpenAPI |
| `frontend-check` | Run frontend type, lint, formatting and component checks |
| `frontend-build` | Regenerate the API contract, then build the frontend |

The Vagrant `vm-*` targets are in the [API usage guide](api-usage.md#vagrant-two-vm-setup).

## Troubleshooting

| Error text | Cause | Fix |
|---|---|---|
| `RuntimeError: StaticFiles directory '<repo>/src/app/../frontend/dist' does not exist.` (and `GET /ui/` returns `Internal Server Error`) | The web UI is not built. | Run step 3: `make frontend-install`, then `npm --prefix src/frontend run build`. |
| `bind: address already in use` from `make up` (Podman prints `listen tcp :443: bind: address already in use`; Docker's wording differs) | Another process holds a host port the stack publishes. Compose always publishes host ports 80 and 443 for Caddy, plus `PORT` (8000) and 2222. | Port 80 or 443: free it, or use the [no-container quickstart](../../README.md#quickstart-no-container). Port 8000: `make PORT=9000 up`. Port 2222: `GITHUB_EMULATOR_SSH_PORT_HOST=2223 make up`. |
| `{"message":"Bad credentials","documentation_url":"https://docs.github.com/rest"}` | The token is wrong, or the server was started with a different `GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN`. | Use `ghp_admin_default_token`, or the value you set. See the [default admin token guide](default-admin-pat.md). |
| ``error: The lockfile at `uv.lock` needs to be updated, but `--locked` was provided.`` | `pyproject.toml` changed without `uv lock` (seen in the CI hygiene job). | Run `uv lock` and commit `uv.lock`. |
