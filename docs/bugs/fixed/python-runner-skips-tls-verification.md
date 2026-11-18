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

- `RunnerClient` verifies against the system roots by default, and honours
  `SSL_CERT_FILE` for a private CA.
- `RUNNER_TLS_VERIFY=false` (also `0` or `no`) turns verification off.
  `docker-compose.yml` sets it for the internal Caddy name, so the compose
  stack behaves as before.
- A failed log upload is logged instead of being silently dropped.
- Tests: `tests/actions/test_runner_tls_verify.py` (default on, the three
  off spellings, other values keep it on). The default-on test fails on the
  old code.
