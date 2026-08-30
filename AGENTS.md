# Agent Instructions

This file is the starting point for agents working in this repository.

## First Reads

Read these files before making changes:

- `README.md` for project purpose, setup, and architecture.
- `PLAN.md` for the current work ledger index.
- `docs/agentic_work_ledger.md` for the project management convention.

When you need detailed status, follow links from `PLAN.md` into:

- `docs/plans/`
- `docs/milestones/`
- `docs/tasks/`
- `docs/bugs/`
- `docs/decisions/`
- `docs/notes/`

## Work Ledger Rules

- Keep `PLAN.md` as a concise index, not a full status document.
- Store durable task details in files under `docs/tasks/`.
- Represent task state by moving files between status directories such as `pending`, `current`, `blocked`, and `done`.
- Record bugs under `docs/bugs/`.
- Record architectural decisions under `docs/decisions/`.
- Update the relevant ledger file with evidence before declaring work complete.

## Project Notes

- This is a FastAPI GitHub API emulator for integration testing.
- Main app wiring lives in `src/app/main.py`.
- API routes live in `src/app/api/`.
- ORM models live in `src/app/models/`.
- Business logic lives in `src/app/services/`.
- Git transport code lives in `src/app/git/`.
- Admin and web UI routes/templates live in `src/app/admin/` and `src/app/web/`.
- Tests live in `tests/`.

## Development Commands

- Install dependencies: `uv pip install -e ".[dev]"`
- Run tests: `uv run pytest tests/ -v`
- Start local server: `uv run uvicorn app.main:app --reload`
- Docker startup: `make up`
- Smoke test running server: `make smoke`

## Default Admin Access

Fresh instances seed the admin user (`admin` / `admin`) and a default admin
personal access token, `ghp_admin_default_token`. API clients can use either
`Authorization: token ghp_admin_default_token` or
`Authorization: Bearer ghp_admin_default_token` immediately after startup.
Startup seeding is controlled by `GITHUB_EMULATOR_SEED_DATA`, which defaults to
`true`. Override the seeded token with `GITHUB_EMULATOR_DEFAULT_ADMIN_TOKEN`.

## Editing Guidance

- Prefer existing project patterns over new abstractions.
- Keep changes scoped to the task.
- Do not remove or rewrite unrelated user changes.
- For route changes, check tests and nearby endpoint behavior for GitHub-compatible response shapes.
- For repository and git behavior, check both API route code and git helper/service code because some responsibilities are duplicated.
