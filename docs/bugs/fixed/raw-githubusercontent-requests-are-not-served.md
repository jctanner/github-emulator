# Bug: requests to raw.githubusercontent.com are not served

Status: fixed 2026-11-15.

## Summary

Some clients hard-code github.com's raw host and fetch
`https://raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>`. On a test
machine that resolves that name to the emulator, the request reached the
emulator with a github.com path shape, which no route served.

Once the name resolves to the emulator, a job that downloads a public file
from that host (an installer script, say) also got a 404, because the
emulator does not hold that repository.

## Fix

- Requests whose `Host` is in `RAW_CONTENT_HOSTS` (default
  `raw.githubusercontent.com`) have their path rewritten to the raw route's
  enterprise shape, `/<owner>/<repo>/raw/<ref>/<path>`, so the same
  visibility check applies. `refs/heads/<name>` and `refs/tags/<name>` refs
  work (`middleware/raw_host.py`).
- With `RAW_CONTENT_UPSTREAM_ADDR` set to the real host's address (looked up
  before the name is pointed at the emulator), a raw-host request for a
  repository the emulator does not have is fetched from that address, with
  the real name for TLS and `Host`, and no credentials forwarded. The
  emulator's own repositories are never fetched upstream, including a name
  that differs from one of them only in letter case. Unset, such a request
  is a 404 as before.
- A test machine that uses this resolves `raw.githubusercontent.com` to the
  emulator, trusts the emulator's certificate for that name, and sets
  `RAW_CONTENT_UPSTREAM_ADDR` as above.

Tests: `tests/test_raw_content.py`.
