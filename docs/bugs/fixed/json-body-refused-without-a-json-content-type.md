# Bug: a JSON body is refused without a JSON Content-Type

Status: fixed 2026-11-15.

## Summary

GitHub's REST API reads a request body as JSON whatever its Content-Type.
`curl -d '{"body":"hi"}' …/issues/1/comments` sends
`application/x-www-form-urlencoded`, and GitHub creates the comment (201).
The same holds for `text/plain`.

FastAPI parses a body as JSON only when the Content-Type says JSON. A route
taking `body: dict`, such as issue comments or pull request reviews,
answered 422 instead. A test fixture that posts with plain `curl -d` failed
against the emulator but passes against GitHub.

## Fix

`JsonBodyMiddleware` handles `/api/v3` POST, PUT, PATCH and DELETE requests
whose Content-Type is missing, form-encoded or `text/plain`. It reads the
body, and if the body is a JSON object or array, it sets the Content-Type to
`application/json` before routing. The middleware leaves these unchanged:
- a body that is not JSON (still 422);
- any other Content-Type;
- release asset uploads, which store the client's Content-Type.

Tests: `tests/test_rest_json_body_and_token_revoke.py`.
