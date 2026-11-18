# Bug: pull request labels, slashed label names, state_reason and hash-algorithm

Status: fixed 2026-11-18.

## Summary

Four REST answers differed from GitHub's:

- A pull request's `labels` were always empty, and the response model
  dropped the key from `GET` and `POST`. A client that gates on a pull
  request's labels never saw one added through `issues/{n}/labels`.
- The per-name label routes answered 404 for a name holding `/` (such as
  `risk/low`), whether sent raw or as `%2F`.
- `PATCH` of an issue that changed `state` without a `state_reason` left
  the reason unset. GitHub sets `completed` on a close and `reopened` on a
  reopen.
- `GET /repos/{owner}/{repo}/hash-algorithm` did not exist.

## Fix

- A pull request's labels come from its issue, and the response includes
  them.
- The label routes accept a name holding `/`, raw or encoded.
- `PATCH` sets `state_reason` to `completed` or `reopened` when it changes
  the state without one.
- `hash-algorithm` answers `sha1`.

Tests: `tests/test_recheck_rest_gaps.py`.
