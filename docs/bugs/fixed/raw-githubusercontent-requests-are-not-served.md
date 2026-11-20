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
  work (`middleware/raw_host.py`). Such a ref is read exactly as given: a
  branch and a tag of the same name each serve their own file, and
  `refs/tags/<name>` for a name that is only a branch is a 404. A short
  `<ref>` resolves as `git rev-parse` does, tags before branches.
- With `RAW_CONTENT_UPSTREAM_ADDR` set to the real host's address (looked up
  before the name is pointed at the emulator), a raw-host request for a
  repository the emulator does not have is fetched from that address, with
  the real name for TLS and `Host`, and no credentials forwarded. The
  emulator's own repositories are never fetched upstream, including a name
  that differs from one of them only in letter case. Unset, such a request
  is a 404 as before.
- The upstream request carries the path percent-encoded as it arrived, so
  `%23`, `%3F` and `%25` in a filename stay part of the filename; the query
  string is not sent on. Proxy and certificate settings from the environment
  are not used; the certificate is checked against certifi's bundle for the
  real name. An upstream redirect (3xx) is answered with 502 rather than
  passed on, and a body over `RAW_CONTENT_UPSTREAM_MAX_BYTES` (default
  100 MiB) is answered with 502; the body is read in chunks up to that cap.
- A test machine that uses this resolves `raw.githubusercontent.com` to the
  emulator, trusts the emulator's certificate for that name, and sets
  `RAW_CONTENT_UPSTREAM_ADDR` as above. Whatever terminates TLS must
  present a certificate for that name: the bundled `Caddyfile` serves only
  `GITHUB_EMULATOR_HOSTNAME`, so a deployment behind it needs a second Caddy
  site for `raw.githubusercontent.com` (for example with `tls internal`)
  that proxies to the app, or the app's own TLS.

Tests: `tests/test_raw_content.py`.
