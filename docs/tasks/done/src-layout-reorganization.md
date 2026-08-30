# Task: Source Layout Reorganization

## Goal

Move production application and runner code under a conventional top-level
`src/` directory without changing runtime behavior.

## Acceptance Criteria

- [x] The FastAPI application lives under `src/app/`.
- [x] The deterministic emulator runner lives under `src/runners/emulator/`.
- [x] The upstream GitHub Actions runner wrapper lives under
  `src/runners/upstream/`.
- [x] Tests, scripts, and Alembic migrations remain top-level support trees.
- [x] Packaging, containers, Compose, Vagrant, deployment scripts, tests, and
  documentation use the new paths.
- [x] Application imports and both runner deployments work after rebuilding.
- [x] The complete test suite passes.

## Status

Complete

## Notes

- `PYTHONPATH`, setuptools package discovery, pytest discovery, and Alembic's
  import path now use `src`.
- Breadboard build scripts now build both runner images from their new source
  directories and copy the relocated emulator runner into the Fullsend image.
- The complete suite passes: 357 tests.
- The GitHub emulator, deterministic repository/config runners, enterprise
  upstream runner, and Fullsend runner images were rebuilt. Live application
  imports, runner registration, heartbeats, and job polling were verified.
