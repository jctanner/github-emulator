# Bug: Concurrent repository mutations return HTTP 500 on SQLite lock

## Summary

Concurrent repository mutations can collide on the emulator's SQLite database
and surface an unhandled HTTP 500 to API clients. The failure was observed while
an end-to-end reset deleted and re-imported repositories in parallel, and a
separate repository-import attempt produced a confirmed
`sqlite3.OperationalError: database is locked` traceback.

## Status

Fixed. The emulator now applies SQLite busy timeouts per connection, retries
bounded short write commits for the observed repository mutation paths, and
returns a structured retryable HTTP 503 response when writer contention
persists.

## Environment

- Date observed: 2026-07-13
- Deployment: Kubernetes namespace `ai-pipeline`
- Service: `github-emulator`
- Storage: SQLite through SQLAlchemy and `aiosqlite`
- Client/orchestrator: Markov project workflow `var-demos-end-to-end`
- Emulator access: in-cluster HTTPS endpoint
  `github-emulator.ai-pipeline.svc.cluster.local`

## Reproduction

The failure was reproduced by the `reset-github` workflow in
`jctanner/ai-first-pipeline` at commit `8397f85`. Its `process_repos` step used
`concurrency: 3`; each branch invokes `import-repo`, which first sends:

```text
DELETE /api/v3/repos/{owner}/{repo}
```

Run the project-sourced reset through Markov:

```bash
deploy/repos/markovd/bin/markovd-cli projects sync ai-first-pipeline --wait
deploy/repos/markovd/bin/markovd-cli runs create var-demos-end-to-end \
  --workflow main \
  --var seed_rfe=true \
  --var run_pipeline=false \
  --wait
```

The failure is timing-dependent. It occurred after the workflow imported the
claim-skill source repository and seeded the claim evaluation repository, when
the next three repository branches began concurrently.

## Expected

Independent repository mutations should either succeed, wait briefly for the
SQLite writer, or receive a bounded retryable response. Normal concurrent API
traffic should not expose an internal database exception as HTTP 500.

## Actual

Markov run `markov-run-776a9fef` ran from
`2026-07-13T14:54:40.806993Z` through
`2026-07-13T14:55:00.295105Z`. Three concurrent `delete_repo` steps all failed
with `http_request: status 500`:

| Markov fork | Repository | Result |
|---|---|---|
| `reset_github-process_repos-agent-eval-harness` | `opendatahub-io/agent-eval-harness` | HTTP 500 |
| `reset_github-process_repos-architecture-context` | `opendatahub-io/architecture-context` | HTTP 500 |
| `reset_github-process_repos-epic-code-gen` | `opendatahub-io/epic-code-gen` | HTTP 500 |

The parent error was:

```text
step "process_repos": step "delete_repo": http_request: status 500
```

The delete responses did not preserve a useful retry hint or structured error
for the orchestrator.

## Confirmed Database-Lock Evidence

A separate reset, `markov-run-510e4400`, ran from
`2026-07-13T14:51:31.817008Z` through
`2026-07-13T14:51:41.184318Z`. Its
`import_claims_skill_source.start_import` request returned HTTP 500 from:

```text
POST /api/v3/admin/repos/import
```

The emulator log recorded the exception at
`src/app/api/users.py:admin_import_repo` ->
`src/app/services/import_service.py:start_single_import` -> `await db.commit()`:

```text
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) database is locked
[SQL: INSERT INTO import_jobs
 (job_type, status, source_url, repo_name, owner_id, parent_job_id,
  error_message, repo_count, completed_count, completed_at)
 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id, created_at]
[parameters:
 ('single', 'pending', 'https://github.com/jctanner/ai-first-pipeline',
  'ai-first-pipeline', 4, None, None, None, 0, None)]
```

This proves that repository-related concurrent writes can exhaust SQLite's
immediate write availability. The DELETE failures have the same timing and
failure shape, but a DELETE-specific traceback was not captured, so their exact
failing SQL statement remains to be confirmed.

## Implementation Context

- `src/app/database.py` enables `PRAGMA journal_mode=WAL` during initialization.
- The async engine's SQLite `connect_args` only set
  `check_same_thread: False`; no connection `timeout` or `PRAGMA busy_timeout`
  is configured.
- `start_single_import()` inserts an `ImportJob` and immediately commits it.
- `admin_import_repo()` catches input `ValueError` only; SQLAlchemy
  `OperationalError` propagates and becomes an unstructured HTTP 500.
- Repository create/delete routes and asynchronous import work may use separate
  sessions while contending for SQLite's single writer.

## Impact

High for integration-test and demo reliability. Parallel bootstrap/reset
workflows are a normal workload for an emulator, but transient write contention
can abort the entire run. Clients cannot distinguish the failure from a
non-retryable server bug, and partially completed reset state makes subsequent
runs harder to reason about.

The issue may affect more than repository deletion and import-job creation;
those are the operations directly observed so far.

## Current Workaround

The end-to-end workflow now:

- imports the claim source before pushing the evaluation corpus;
- briefly allows the emulator write transaction to settle; and
- runs `process_repos` with `concurrency: 1` instead of `3`.

The repository-import serialization was committed in
`jctanner/ai-first-pipeline` as `e2017b9`. This reduces concurrency in the
client but does not fix the emulator's API behavior.

## Suggested Investigation

1. Add a focused concurrent-write regression test covering repository DELETE
   and `POST /admin/repos/import` from independent async sessions.
2. Confirm whether every SQLite connection receives WAL mode and a useful
   `busy_timeout`, rather than setting WAL only during initialization.
3. Evaluate a bounded retry around short write transactions when SQLite raises
   the retryable `database is locked` condition.
4. Ensure sessions roll back before retrying and do not duplicate import jobs
   or repository side effects.
5. If contention remains after bounded retries, return a structured retryable
   response (for example, HTTP 503 with `Retry-After`) instead of an unhandled
   HTTP 500.
6. Re-run the Markov reset with repository concurrency restored to at least
   three and verify repeated clean runs.

## Acceptance Criteria

- [x] A deterministic test reproduces concurrent repository write contention.
- [x] Concurrent DELETE/import requests no longer expose an unhandled HTTP 500
      for transient SQLite locks.
- [x] Any retry is bounded, rolls back failed transactions, and does not create
      duplicate import jobs or partial repository state.
- [x] A retryable exhaustion response is structured and documented.
- [x] The local podman-compose verification succeeds with repository mutation
      concurrency restored to `3` or greater.
- [x] Tests cover both the import-job INSERT path and repository deletion.

## Fix

- Added per-connection SQLite `timeout` and `PRAGMA busy_timeout` configuration.
- Added a bounded SQLite write retry helper that rolls back failed transactions
  before retrying and returns a structured retryable response after exhaustion.
- Made personal-access-token `last_used_at` updates best-effort under SQLite
  writer contention so authentication does not fail before the requested
  mutation can return a useful response.
- Applied bounded retry to:
  - `start_single_import()` for import-job creation.
  - repository deletion in `repo_service.delete_repo()`.
- Added a GitHub-style HTTP 503 response with:
  - `Retry-After: 1`
  - `errors: [{"resource": "Database", "code": "sqlite_locked"}]`
- Added `podman-compose.local.yml` for rootless Podman verification without
  privileged host port bindings.

## Verification

### Automated Tests

```bash
uv run pytest tests/test_sqlite_lock_handling.py -v
uv run pytest tests/test_sqlite_lock_handling.py tests/test_repos_api.py::test_delete_repo tests/test_admin.py -q
uv run pytest tests/test_sqlite_lock_handling.py tests/test_repos_api.py tests/test_admin.py tests/test_pulls_api.py -q
git diff --check
```

Results:

- SQLite lock regression tests passed: 2 passed, 1 warning.
- Focused repo/admin set passed: 18 passed, 5 warnings.
- Broader repo/admin/PR set passed: 47 passed, 5 warnings.
- Diff whitespace check clean.

### Local podman-compose Reproduction

Started the local stack with rootless-safe ports:

```bash
.venv/bin/podman-compose -f podman-compose.local.yml up -d --build github-emulator
.venv/bin/podman-compose -f podman-compose.local.yml up -d --force-recreate github-emulator
```

Pre-fix reproduction under an intentionally held SQLite writer lock:

- `DELETE /api/v3/repos/admin/sqlite-lock-delete-repro-2` returned
  `HTTP_STATUS:500`.
- `POST /api/v3/admin/repos/import` returned `HTTP_STATUS:500`.
- Logs showed `sqlite3.OperationalError: database is locked`.

Post-fix verification under the same held SQLite writer lock:

- `POST /api/v3/admin/repos/import` returned `HTTP/2 503` with
  `Retry-After: 1` and `code: sqlite_locked`.
- `DELETE /api/v3/repos/admin/sqlite-lock-delete-fixed-2` returned
  `HTTP/2 503` with `Retry-After: 1` and `code: sqlite_locked`.
- Retrying the same delete after releasing the lock returned
  `HTTP_STATUS:204`, proving no partial delete state remained.

Local concurrency-three verification without an artificial lock:

```text
delete-burst-c:204
delete-burst-b:204
delete-burst-a:204
import-repo-two:202
import-repo-one:202
import-repo-three:202
```

Recent compose logs showed the expected `503 Service Unavailable` responses for
held-lock exhaustion and `204`/`202` responses for the concurrency-three burst.
The original Markov Kubernetes workflow was not rerun as part of this local
podman-compose fix verification.
