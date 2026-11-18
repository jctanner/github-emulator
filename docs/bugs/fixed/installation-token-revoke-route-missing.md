# Bug: an installation token cannot revoke itself

Status: fixed 2026-11-15.

## Summary

GitHub documents "Revoke an installation access token":
`DELETE /installation/token`, authenticated with the token itself, answers
204. Clients that mint a short-lived token revoke it when their work ends and
treat any other status as a failure. The emulator had no such route and
answered 404.

## Fix

The route deletes the calling token's row and answers 204 with no body. From
then on the token no longer authenticates. A personal access token gets 403,
as on `GET /installation/repositories`.

Tests: `tests/test_rest_json_body_and_token_revoke.py`.
