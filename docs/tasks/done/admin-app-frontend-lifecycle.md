# Task: Complete GitHub App Admin Frontend Lifecycle

## Goal

Provide the App-management controls needed by the local Fullsend setup without
depending on the external GitHub App frontend PR.

## Completed

- Added emulator client-ID metadata to App API and admin views.
- Added App deletion with explicit installation and token cleanup.
- Added installation removal with token cleanup.
- Added private-key regeneration with one-time display of the replacement key.
- Added delete/remove/regenerate controls and confirmation prompts to the admin UI.
- Preserved repository/permission selection and redacted key/token metadata.
- Added authenticated `/admin/api/apps` compatibility endpoints for JSON setup,
  key retrieval/rotation, and installations.
- Added GitHub-style App and installation metadata fields to JWT-authenticated
  API responses.
- Persisted App client IDs and added SQLite backfill/indexing for existing
  emulator databases.
- Added `GITHUB_EMULATOR_APP_JWT_PERMISSIVE` (default `true`) with a strict
  signature-verification mode for production-like testing.
- Unified PAT, Basic-auth, middleware, and FastAPI dependency handling for
  `ghs_` installation tokens, including expiry checks.
- Marked commit and tag verification as valid for installation-token requests,
  while preserving unsigned responses for ordinary PAT requests.
- Made the local Compose HTTP port and container engine configurable through
  `PORT` and `CONTAINER_ENGINE`; uvicorn now binds directly on `0.0.0.0`.

## Evidence

- `./.venv/bin/python -m pytest tests/test_admin_apps.py -q`
- Result: 7 passed.
- `./.venv/bin/python -m pytest tests/ -q`
- Result: 276 passed, 557 warnings.

## Notes

The client ID is derived as `Iv1.<app_id>` because the emulator does not
implement OAuth and has no separate OAuth client database record. It is labeled
as emulator-only in the UI.
