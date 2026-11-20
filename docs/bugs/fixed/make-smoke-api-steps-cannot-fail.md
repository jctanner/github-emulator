# Bug: `make smoke` API steps cannot fail the target

Status: fixed 2026-11-20 (issue #82).

## Summary

- Each API step in `make smoke` ended `| python3 -m json.tool | head -5`, so
  the recipe line's status was `head`'s: 0 even when `curl -sf` failed (401,
  404, 422, or no server).
- A second run against the same stack got `422` on `POST /user/repos`
  (`smoke-repo` already exists). The first point hid it, so a re-run checked
  less than a first run.
- The token was fetched with `$(eval TOKEN := $(shell curl ...))` during
  recipe expansion, so even `make -n smoke` created a `smoke-token`.

## Fix

- Each API step writes the body with `curl -sS -o` and the status with
  `-w '%{http_code}'`, then the `smoke_status` / `smoke_expect` helpers exit 1
  unless the status is the expected one. No Make-wide `pipefail` is used:
  `.SHELLFLAGS` is not in GNU Make 3.81, and `head -5` closing the pipe early
  could fail healthy steps.
- `422` on repository creation is taken as "already exists" only when
  `GET /repos/admin/smoke-repo` then answers `200`.
- Each `curl` substitution is followed by `smoke_xfer`, which exits 1 when
  curl itself failed (for example a truncated body) even if it printed a
  status.
- The token is fetched in a recipe line and kept in `/tmp/smoke-api/token`
  for the later lines (each runs in its own shell). The directory is created
  private (`umask 077`, `mkdir -m 700`, which fails if it cannot be freshly
  created) and is removed at the start of the run, at the end, and on every
  failing step. `make -n smoke` runs nothing.

## Evidence

The sandbox used for the fix blocks both `make` spawning a shell and requests
to `localhost`. So the recipe was checked by running each logical line of
`make -n smoke` in its own `sh -c`, stopping at the first failure as make
does, with `curl` and `git` stubbed:

- fresh stack (all 2xx): exit 0, `PASS: File content verified`.
- re-run (`POST /user/repos` answers 422, `GET` the repo answers 200): exit 0.
- `GET /user` answers 401: `FAIL: HTTP 401, expected 200`, exit 1.
- no server (curl exit 7, status `000`): `FAIL: HTTP 000, expected 201` at
  the token step, exit 1.
- 422 on create, then 404 on `GET` the repo: `FAIL: HTTP 404, expected 200`,
  exit 1.
- curl exits 18 after printing a 2xx status: `FAIL: API transfer failed`,
  exit 1, token directory removed.
- `make -n smoke` only prints the commands.

A run against a real stack (`make up && make smoke && make smoke`) was not
possible in that sandbox.
