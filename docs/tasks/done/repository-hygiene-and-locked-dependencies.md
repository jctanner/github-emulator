# Task: Repository hygiene and locked dependencies

## Goal

Give the repository the checks a change must pass, and install the same,
hash-checked dependency set everywhere.

## Context

The repository had no pre-commit config, no Python lint configuration, no
secret scanning and no update bot. `uv.lock` was in `.gitignore`, and the
images installed `requirements.txt`, which held only `>=` floors and no
hashes: each build took the newest release of every package that day. Caddy
was an unpinned download from `caddyserver.com/api/download` with no
checksum.

## Done

- `.pre-commit-config.yaml`, pinned by commit: the standard file checks, ruff
  (lint only), gitleaks, shellcheck, actionlint, zizmor, and the frontend's
  eslint and prettier. It runs on the files a change touches.
- Ruff config in `pyproject.toml`; each ignored rule has its reason beside it.
- `.gitleaks.toml` allows only the documented default admin token.
- `uv.lock` committed. The images export it (`uv export --frozen --no-dev`)
  and install with `pip --require-hashes`; `requirements.txt` is removed. uv
  comes from `ghcr.io/astral-sh/uv` pinned by digest, and supervisord is the
  Debian package the images already installed.
- Caddy v2.11.7 from the GitHub release, checked against its SHA-512.
- `.github/workflows/hygiene.yml`: pre-commit on the change, gitleaks over the
  full history, pip-audit on the lock, `npm audit --audit-level=high`, and the
  frontend's lint, typecheck, format and tests. The Python tests stay in
  `ci.yml`.
- `.github/dependabot.yml`: uv, npm, Actions and Docker, weekly, with a 14-day
  cooldown.
- Frontend: in-range `npm audit` fixes and vitest 4.1.12, clearing the
  critical and high advisories.
- `CONTRIBUTING.md` describes the setup, the checks and the policies.
- The files touched meet the lint rules; committed browser-automation logs and
  a stray screenshot are removed.
- Related bug: `docs/bugs/fixed/python-runner-skips-tls-verification.md`.

## Validation

- `uv run pytest tests/ -q` passes.
- `pre-commit run --from-ref origin/main --to-ref HEAD` passes.
- Both images build with podman from the locked set.

## Open

- No formatter yet: `ruff format` would change most files, so it is a
  one-time change for a moment with no open branches.
- `python-jose` has an advisory with no fixed release (PYSEC-2026-4298). It
  is not reachable here, and CI ignores it by ID with the reason beside it.
  Moving to PyJWT or joserfc is the durable fix.
