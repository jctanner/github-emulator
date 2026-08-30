# Task: Stream Live Actions Job Logs

## Goal

Expose stdout/stderr from long-running Actions jobs while they are executing,
and update the repository job page without a manual reload.

## Acceptance Criteria

- [x] The bundled runner uploads output incrementally while a step is running.
- [x] The existing append-only log endpoint remains compatible with one-shot uploads.
- [x] The job page polls a scoped live-state endpoint while the job is active.
- [x] Status, step state, timestamps, and logs update in the browser.
- [x] Tests cover pre-completion log upload and live endpoint state/log output.

## Implementation

- `src/runners/emulator/runner.py` streams shell subprocess output and uploads chunks.
- `src/app/web/routes.py` adds the repository-scoped `/actions/jobs/{id}/live` endpoint.
- `src/app/web/templates/action_job_detail.html` polls that endpoint once per second.

## Evidence

- `uv run python -m pytest tests/test_web_actions.py tests/test_runner_live_logs.py -q`: 9 passed.
- `uv run python -m pytest tests/test_actions_execution.py tests/test_actions_api.py tests/test_web_actions.py tests/test_runner_live_logs.py -q`: 18 passed.
- GitHub emulator and both runner image layers rebuilt/imported; `github-actions-runner` rollout completed.
- Live UI page and `/actions/jobs/378/live` endpoint smoke-tested at `github.local`.
