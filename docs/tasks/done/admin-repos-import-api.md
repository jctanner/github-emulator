# Add admin API endpoint for repo imports

## Summary

Add a `POST /api/v3/admin/repos/import` bootstrap endpoint that wraps the existing `import_service.py` functions, matching the pattern of the existing `/admin/users` and `/admin/tokens` endpoints. This lets automated pipelines import repos from GitHub without cloning and pushing locally.

## Context

The emulator already has a full import system:
- `src/app/services/import_service.py` — `start_single_import()` and `start_bulk_import()` clone bare repos from GitHub server-side, create DB records, and sync branches
- `src/app/admin/routes.py` lines 807-906 — admin web UI form at `POST /admin/import` that calls the import service
- `src/app/models/import_job.py` — `ImportJob` model tracking import status

But there's no API equivalent — only the web form. The existing admin bootstrap endpoints in `src/app/api/users.py` (`/admin/users`, `/admin/tokens`) are unauthenticated and follow a consistent pattern.

## Desired API

### `POST /api/v3/admin/repos/import`

Unauthenticated bootstrap endpoint (same as `/admin/users` and `/admin/tokens`).

**Request body:**

```json
{
  "url": "https://github.com/opendatahub-io/odh-dashboard",
  "owner": "opendatahub-io",
  "github_token": "ghp_..."
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `url` | Yes | GitHub HTTPS URL of the repo to import (e.g., `https://github.com/owner/repo`) |
| `owner` | Yes | Login of the local user who will own the imported repo. Must already exist (created via `/admin/users`). |
| `github_token` | No | GitHub PAT for private repos or to avoid rate limits. Injected into the clone URL. |

**Response (202 Accepted):**

```json
{
  "job_id": 5,
  "status": "pending",
  "source_url": "https://github.com/opendatahub-io/odh-dashboard",
  "repo_name": "odh-dashboard",
  "owner": "opendatahub-io"
}
```

### `GET /api/v3/admin/repos/import/{job_id}`

Check import job status.

**Response:**

```json
{
  "job_id": 5,
  "status": "completed",
  "source_url": "https://github.com/opendatahub-io/odh-dashboard",
  "repo_name": "odh-dashboard",
  "owner": "opendatahub-io",
  "error_message": null,
  "created_at": "2026-07-02T13:00:00Z",
  "completed_at": "2026-07-02T13:00:45Z"
}
```

Status values: `pending`, `running`, `completed`, `failed`.

## Implementation notes

- Add the routes in `src/app/api/users.py` alongside the existing admin helpers (or a new `src/app/api/admin.py` — either is fine)
- Look up the owner by login via `User.login`, resolve `owner_id`
- Call `start_single_import(db, url, owner_id, github_token)` from `src/app/services/import_service.py`
- The import runs async in the background (the service already uses `asyncio.create_task`), so the POST returns immediately with the job ID
- The status endpoint reads the `ImportJob` model

## Usage example

```bash
# 1. Ensure owner exists
curl -s -X POST "http://github.local/api/v3/admin/users" \
  -H "Content-Type: application/json" \
  -d '{"login": "opendatahub-io", "email": "odh@example.com", "password": "x"}'

# 2. Import a repo
curl -s -X POST "http://github.local/api/v3/admin/repos/import" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://github.com/opendatahub-io/odh-dashboard", "owner": "opendatahub-io"}'
# → {"job_id": 1, "status": "pending", ...}

# 3. Poll until done
curl -s "http://github.local/api/v3/admin/repos/import/1"
# → {"job_id": 1, "status": "completed", ...}
```

## Status

Done

## Evidence

- Added `POST /api/v3/admin/repos/import` in `src/app/api/users.py`.
- Added `GET /api/v3/admin/repos/import/{job_id}` in `src/app/api/users.py`.
- Added focused API coverage in `tests/test_admin.py`.
- Verified changed files compile: `uv run python -m py_compile src/app/api/users.py tests/test_admin.py`.
- Ran a temporary ASGI smoke check for the new POST and GET endpoints; POST returned `202` and GET returned `200`.
- Installed dev dependencies with `uv pip install -e ".[dev]"` after `pytest` was missing.
- Verified focused coverage passes: `uv run pytest tests/test_admin.py -v` completed with 13 passed.
