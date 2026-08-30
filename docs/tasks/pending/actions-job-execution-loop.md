# Task: Actions Job Execution Loop

## Goal

Prove and implement the minimum real execution path for workflow jobs, with the
real `actions/runner` binary as the preferred compatibility target.

## Context

The current custom Python runner registers, polls, reports progress, executes
local shell `run:` steps, uploads logs, and marks steps complete. It remains a
fallback because it does not implement the upstream `actions/runner` runtime or
protocol.

The desired long-term outcome is maximum compatibility through the real
`actions/runner` binary. The Python runner should remain available as a
simulation and development fallback unless there is a deliberate decision to
expand it.

## Acceptance Criteria

- [x] Build or document a Docker image/service variant that runs the real
      `actions/runner` binary against the emulator.
- [ ] Verify real runner registration against the emulator.
- [ ] Verify real runner session/message polling against the emulator.
- [ ] Send job payloads rich enough for the real runner to execute at least a
      simple shell step.
- [ ] Capture real runner timeline updates, logs, job completion, and failure
      results.
- [x] Preserve enough workflow/job/step metadata in the database to support the
      real runner payloads and the Web UI.
- [x] Keep the Python runner usable as a simulation fallback.
- [x] Add tests or a repeatable smoke script for success, failure, skipped
      dependent jobs, and log capture.

## Files Likely Involved

- `src/app/services/workflow_service.py`
- `src/app/models/actions.py`
- `src/app/api/actions_dispatch.py`
- `src/app/api/actions_pipelines.py`
- `src/app/api/actions_distributed_task.py`
- `src/runners/emulator/runner.py`
- `src/runners/emulator/Dockerfile`
- `docker-compose.yml`
- `tests/actions/test_execution.py`

## Open Design Question

How much of the GHES/Azure Pipelines runner protocol must be implemented before
the real `actions/runner` can execute a simple local job?

## Status

Pending

## Notes

- This has security implications because workflow code is user-controlled.
- If enabled by default, the runner should be clearly scoped to local/testing
  environments only.
- Do not grow the Python runner into a full Actions runtime unless the real
  runner path is proven infeasible or too expensive for the project's needs.
- Added `src/runners/upstream/Dockerfile`, `src/runners/upstream/entrypoint.sh`, and the
  `actions-real-runner` compose profile as the concrete real-runner spike
  artifact.
- Workflow job step JSON now preserves `run`, `shell`, `env`,
  `working-directory`, `uses`, and `with` keys so the server no longer discards
  the minimum payload needed by a runner.
- The Python fallback runner now executes local shell `run:` steps and uploads
  captured logs, but this does not prove upstream `actions/runner`
  compatibility.
- Added pool-scoped distributed-task endpoints matching the shape in
  `specs/github-actions.md`:
  sessions, messages, job request accept/update, timelines, timeline records,
  and timeline log upload.
- Added `tests/actions/test_execution.py` coverage for:
  custom runner success and log capture, failure with skipped dependent jobs,
  and pool-scoped protocol registration/session/message/timeline/log/completion.
- Verification: `uv run --with pytest --with pytest-asyncio pytest tests/ -v`
  passed 234 tests.
