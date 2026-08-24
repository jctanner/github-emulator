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

## Evidence

- `uv run python -m pytest tests/test_admin_apps.py -q`
- Result: 4 passed.
- `uv run python -m pytest tests/ -q`
- Result: 273 passed, 21 warnings.

## Notes

The client ID is derived as `Iv1.<app_id>` because the emulator does not
implement OAuth and has no separate OAuth client database record. It is labeled
as emulator-only in the UI.
