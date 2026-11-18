# Bug: a narrowed installation token reports "all" and ignores repository_ids

Status: fixed 2026-11-14.

## Summary

`POST /app/installations/{id}/access_tokens` takes `repositories` (names) and
`repository_ids` to narrow a token to some of the installation's
repositories. GitHub's docs define `repository_selection` `"selected"` as
access to selected repositories; the emulator reports it for a narrowed
token. The docs do not say what giving both lists does; the emulator grants
their union.

The emulator:

- reported `"all"` for a token narrowed by name on an installation with
  access to all repositories. Scope enforcement was correct; only the field
  was wrong, so a client that checks it (a token mint that warns when it got
  a broader token than it asked for) saw a false widening;
- did not read `repository_ids` at all. A token asked for by id reported
  `"all"`, listed no repositories, and could write to every repository of
  the installation.

## Fix

- `repository_ids` are resolved to full names, and the token gets the union
  of both lists. An unknown id, an id that is not a positive 64-bit integer,
  and a field that is present but not a list (`""`, `0`, `{}`) are refused
  with 422, so a value of the wrong type cannot get an un-narrowed token.
  A missing field, `null` or an empty list does not narrow the token, as
  before; GitHub's docs don't say what an empty list does.
- A token narrowed by either parameter reports `"selected"`. A token that is
  not narrowed, on an installation with access to all repositories, stays
  `"all"`.

Tests: `tests/test_installation_token_selection.py`.
