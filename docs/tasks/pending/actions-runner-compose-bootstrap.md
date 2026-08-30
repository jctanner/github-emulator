# Task: Actions Runner Compose Bootstrap

## Goal

Make the `actions-runner` service in `docker-compose.yml` usable without manual
guesswork.

## Context

`docker-compose.yml` already contains an `actions-runner` service. It uses the
custom Python runner in `src/runners/emulator/runner.py` and expects:

- `GITHUB_EMULATOR_URL=https://github-emulator`
- `GITHUB_EMULATOR_TOKEN=${GITHUB_EMULATOR_RUNNER_TOKEN:-}`
- `RUNNER_REPO=${RUNNER_REPO:-admin/test-repo}`

The runner cannot register unless a valid admin PAT is supplied through
`GITHUB_EMULATOR_RUNNER_TOKEN`, and the default repo may not exist.

## Acceptance Criteria

- [x] Document the exact bootstrap sequence for `docker compose up` with the
      runner enabled.
- [x] Provide a Makefile target or script that creates the runner token and
      writes an `.env` value or prints export commands.
- [x] Document how to choose `RUNNER_REPO`.
- [x] Runner service fails clearly when token or repo is missing.
- [ ] Smoke test path proves runner registration, heartbeat, job polling, job
      completion, and UI visibility.
- [x] Smoke test starts from Docker Compose and records the exact command
      sequence.
- [x] Playwright desktop validation can run against the compose-served UI after
      the runner creates or completes at least one job.

## Files Likely Involved

- `docker-compose.yml`
- `Makefile`
- `src/runners/emulator/runner.py`
- `README.md`
- `scripts/`
- Playwright smoke script or pytest integration once the project chooses the
  test harness.

## Status

Pending

## Notes

- This task should happen after the UI can show runner state, otherwise the
  bootstrap success signal is only log output.
- Implemented `scripts/actions-compose-bootstrap.sh`, `make
  actions-runner-env`, and README instructions for default and custom
  `RUNNER_REPO` values.
- Added `scripts/actions-ui-smoke-playwright.py` and `make actions-ui-smoke`
  for desktop UI validation once a compose stack is running.
- Live Docker Compose validation remains open in this environment because the
  available Docker/Podman installation has no compose provider.
