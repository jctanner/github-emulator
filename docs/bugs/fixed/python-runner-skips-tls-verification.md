# Python runner skips TLS verification

## Status: Fixed

## Summary

The Python runner (`src/runners/emulator/runner.py`) built its HTTP client
with `httpx.Client(verify=False)`. It sent its registration token, its admin
token and every job token to whatever server answered on the emulator's
address, without checking the certificate. The actions/runner that GitHub
ships verifies the server's certificate.

## Cause

`verify=False` was hard-coded, most likely because the compose stack serves
the emulator through Caddy's internal CA, which the runner image does not
trust.

## Fix

- `RunnerClient` verifies by default. httpx checks the certificate against
  certifi's bundle, or against `SSL_CERT_FILE` (or `SSL_CERT_DIR`) instead
  when set. It does not read the OS trust store, so a private CA goes in
  `SSL_CERT_FILE`.
- `RUNNER_TLS_VERIFY=false` (also `0` or `no`) turns verification off and
  keeps the old behaviour. The shipped runner configurations set it
  explicitly, because their emulator address is not a name the certificate
  carries: `docker-compose.yml` (`https://github-emulator`) and the
  `Vagrantfile` runner (`https://192.168.123.10`). Each overrides from the
  environment.
- A failed log upload is logged instead of being silently dropped.
- Tests: `tests/actions/test_runner_tls_verify.py` (default on, the three
  off spellings, other values keep it on). The default-on test fails on the
  old code.
