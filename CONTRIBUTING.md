# Contributing

This guide shows you how to pass the checks required for your change; start by completing the one-time local setup below.

## Prerequisites

Run these commands once from the repository root:

1. Create the virtual environment and install Python dependencies:
   ```bash
   uv venv && uv pip install -e ".[dev]"
   ```

2. Install pre-commit hooks (scripts that run automated checks before git records a commit):
   ```bash
   pre-commit install
   ```
   ```
   pre-commit installed at .git/hooks/pre-commit
   ```

3. Install frontend dependencies (needed only for the two frontend hooks):
   ```bash
   npm --prefix src/frontend ci
   ```

## Check your change

Run the hooks on the files your change touches before submitting a pull request:
```bash
pre-commit run --from-ref origin/main --to-ref HEAD
```

```
check for added large files..............................................Passed
check for merge conflicts................................................Passed
check toml...............................................................Passed
check yaml...............................................................Passed
fix end of files.........................................................Passed
trim trailing whitespace.................................................Passed
detect private key.......................................................Passed
ruff check...............................................................Passed
Detect hardcoded secrets.................................................Passed
shellcheck...............................................................Passed
Lint GitHub Actions workflow files.......................................Passed
zizmor...................................................................Passed
frontend eslint..........................................................Passed
frontend prettier........................................................Passed
```

## CI Checks

Continuous integration in [.github/workflows/hygiene.yml](.github/workflows/hygiene.yml) runs on every push and pull request:
- pre-commit on the change: a pull request is checked against its base, a push against the commit it replaced.
- gitleaks over the full git history.
- pip-audit on the locked Python dependencies (`uv.lock`).
- `npm audit --audit-level=high` for the frontend.
- frontend lint, typecheck, format check and tests.

## Policies

### Lint Policy
Ruff lints Python code (no formatter yet). Because hooks run on touched files, a file you edit must pass ruff even for findings that were there before you. Most are fixed by:
```bash
uvx ruff@0.17.3 check --fix <file>
```
Rules and deliberate exceptions are in [pyproject.toml](pyproject.toml) with the reason beside each.

### Dependencies and Security Advisories
- `uv.lock` is committed. After changing dependencies in [pyproject.toml](pyproject.toml), run `uv lock` and commit `uv.lock`. CI runs `uv lock --locked`, which fails if the lock is out of date.
- Everything installs from `uv.lock` with hashes: `uv sync --frozen` locally, and the container images export the lock (`uv export --frozen --no-dev`) and install with `--require-hashes`. There is no `requirements.txt` to keep in step.
- Dependabot opens weekly update pull requests, holding new releases back 14 days ([.github/dependabot.yml](.github/dependabot.yml)).
- An advisory with no fix that cannot be reached here is accepted by adding `--ignore-vuln <ID>` in [.github/workflows/hygiene.yml](.github/workflows/hygiene.yml) with a comment saying why it is unreachable. Two are accepted today: PYSEC-2026-4298 (`python-jose`; every decode pins its algorithm) and PYSEC-2026-1325 (`ecdsa`; nothing signs with ECDSA).

### Secrets
Gitleaks allows exactly one value, the documented default admin token `ghp_admin_default_token` ([.gitleaks.toml](.gitleaks.toml)). Any other hit fails the commit and CI.

## Troubleshooting

| Error text | Cause | Fix |
|---|---|---|
| ``[ERROR] Cowardly refusing to install hooks with `core.hooksPath` set.`` | Your git config sets a global hooks directory (for example a machine-wide git guard). | Leave it. Before you push, run `pre-commit run --from-ref origin/main --to-ref HEAD` yourself. |
| `ruff check....Failed` with `I001` or `F401` | Import order or an unused import in a file you touched. | `uvx ruff@0.17.3 check --fix <file>` |
| `Detect hardcoded secrets....Failed` | A value that looks like a credential. | Remove it. If it is a published test value, add it to `.gitleaks.toml` with a comment. |
| ``error: The lockfile at `uv.lock` needs to be updated, but `--locked` was provided.`` | `pyproject.toml` changed without `uv lock`. | Run `uv lock` and commit `uv.lock`. |
| `sh: eslint: command not found` | Frontend packages are not installed. | `npm --prefix src/frontend ci` |
