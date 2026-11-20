# Bug: the rate limit is fixed at 5000 and /rate_limit ignores usage

Status: fixed 2026-11-15.

## Summary

The emulator reports github.com-style rate-limit headers per token and hour,
with the limit hard-coded at 5000. It never refuses a request, but clients
that pace themselves on `X-RateLimit-Remaining` do. Fullsend's pool gate
stops leasing slots at `remaining=0` until the reset. In a 46-scenario
behaviour run, one admin token used its 5000 and all 6 workers sat idle for
18 m 20 s (about 1100 s of a 4493 s suite).

`GET /rate_limit` always answered `remaining: 5000`, whatever the headers
said, and counted against the limit itself; GitHub does not count it.

## Fix

- `RATE_LIMIT` setting (`GITHUB_EMULATOR_RATE_LIMIT`), default 5000, used by
  the headers and by `GET /rate_limit`.
- `GET /rate_limit` reports the caller's own usage and reset (core, GraphQL
  and `rate`), and does not count against the limit.

## Evidence

- `tests/test_misc_api.py::test_rate_limit_is_configurable`: with 1,000,000
  the headers and `/rate_limit` agree on limit, used and remaining.
- Fullsend behaviour run 43683231672 (waynesun09/fullsend): 410 "no capacity …
  rate-limited: halfsend until 07:00:33Z" lines from 06:42:13 to 07:00:33.
