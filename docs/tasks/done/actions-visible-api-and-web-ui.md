# Task: Actions Visible API and Web UI

## Goal

Expose enough GitHub Actions state in the REST API and Web UI for users to
visualize workflows, runs, jobs, steps, runner assignment, and available logs.

## Context

The backend already creates workflow/run/job rows and has runner dispatch
endpoints, but the Web UI does not expose any of this. The current UI repo
navigation only includes Code, Issues, and Pull requests.

## Acceptance Criteria

- [x] Repository navigation includes an Actions tab.
- [x] `/ui/{owner}/{repo}/actions` lists workflows and recent workflow runs.
- [x] `/ui/{owner}/{repo}/actions/runs/{run_id}` shows run metadata, jobs,
      status, conclusion, commit SHA, branch, actor, and event.
- [x] `/ui/{owner}/{repo}/actions/jobs/{job_id}` shows job metadata, steps,
      runner name/id, timestamps, status, conclusion, and logs when present.
- [x] `/ui/{owner}/{repo}/actions/runners` lists repository runners with name,
      labels, status, busy state, OS, and last heartbeat when available.
- [x] API surface includes any missing read endpoints the UI needs, especially
      a single job endpoint and a job log endpoint.
- [x] Empty states are useful for repos with no workflows, no runs, no jobs, or
      no registered runners.
- [x] Existing private repository visibility rules are respected.
- [x] Tests cover the Actions tab, run list, run detail, job detail, runner
      list, and log display/read endpoint.
- [x] Playwright desktop validation covers the Actions tab, run list, run
      detail, job detail, runner list, empty states, and log display.
- [x] Validation runs against a live server and a compose-targeted smoke script
      exists. Compose execution itself could not be run in this environment
      because no Docker/Podman compose provider is installed.

## Files Likely Involved

- `src/app/web/routes.py`
- `src/app/web/templates/_repo_nav.html`
- `src/app/web/templates/actions.html`
- `src/app/web/templates/action_run_detail.html`
- `src/app/web/templates/action_job_detail.html`
- `src/app/web/templates/action_runners.html`
- `src/app/web/static/css/web.css`
- `src/app/api/actions.py`
- `src/app/api/actions_runners.py`
- `tests/test_actions_api.py`
- `tests/test_web_actions.py`
- `tests/test_web_actions_playwright.py` or `scripts/actions-ui-smoke.*`

## Design Notes

- Prefer read-only views first; controls such as rerun/cancel can follow after
  state visualization is stable.
- Keep the UI dense and repository-oriented, matching the existing Primer-based
  web templates.
- Use status labels consistently:
  `queued`, `waiting`, `in_progress`, `completed`.
- Use conclusion labels consistently:
  `success`, `failure`, `cancelled`, `skipped`, `neutral`, `timed_out`.
- Make logs optional because current log upload writes files under
  `{DATA_DIR}/logs/jobs/{job_id}.log`.
- Playwright coverage is desktop-only unless a later task explicitly adds
  mobile requirements.

## Status

Done

## Notes

- The first implementation can visualize simulated jobs. It does not need to
  prove real shell execution.
- Add a task note if the current database model lacks fields needed by the UI
  instead of overloading unrelated columns.
- Use the compose stack as the end-to-end validation target so the runner,
  service URLs, cookies, static assets, and templates are checked together.

## Evidence

- Added Actions API read endpoints for single job detail and job logs.
- Added Web UI routes and templates for:
  - `/ui/{owner}/{repo}/actions`
  - `/ui/{owner}/{repo}/actions/runs/{run_id}`
  - `/ui/{owner}/{repo}/actions/jobs/{job_id}`
  - `/ui/{owner}/{repo}/actions/runners`
- Added desktop Playwright smoke script:
  `scripts/actions-ui-smoke-playwright.py`.
- Added compose runner bootstrap:
  `scripts/actions-compose-bootstrap.sh`, `make actions-runner-env`, and
  `make actions-ui-smoke`.
- Verification:
  - `uv run --with pytest --with pytest-asyncio pytest tests/ -v`
    passed: 234 tests.
  - `bash -n scripts/actions-compose-bootstrap.sh` passed.
  - `python -m py_compile scripts/actions-ui-smoke-playwright.py` passed.
  - Playwright MCP desktop validation against a live local server rendered
    Actions list, run detail, job detail with logs, and runners pages.
  - `docker compose config` could not run in this environment because Docker
    has no compose provider installed.
  - Local Uvicorn validation required aligning `pyproject.toml` with the
    existing runtime pins for `bcrypt`, `pyyaml`, and `asyncssh`.
