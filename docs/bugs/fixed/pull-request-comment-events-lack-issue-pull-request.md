# Bug: a pull request's issue events lack `issue.pull_request`

Status: fixed 2026-11-08.

## Summary

On GitHub, an issue that is a pull request carries a `pull_request` object
in event payloads, as it does in the REST API. Workflows test
`github.event.issue.pull_request` to tell a comment on a pull request from
one on an issue.

The emulator's event payload builder `_issue_payload` never set it. Every
pull request comment therefore read as an issue comment.

Fullsend's dispatch computes `ISSUE_IS_PR` from that field. With it false,
`/fs-fix` and `/fs-review` on a pull request matched no stage. In Breadboard's
behaviour-test T2 tier, this failed two scenarios:

- `fix-authorization.feature` ("/fs-fix starts fix for the write actor");
- `review-cel-route.feature`.

## Fix

`_issue_payload` adds `pull_request` (`url`, `html_url`, `diff_url`,
`patch_url` and `merged_at`) when the issue has a pull request loaded, in the
REST API's shape. A plain issue still omits the key.

Tests: `tests/test_webhook_delivery.py`
(`test_a_comment_on_a_pull_request_marks_its_issue_as_one`).
