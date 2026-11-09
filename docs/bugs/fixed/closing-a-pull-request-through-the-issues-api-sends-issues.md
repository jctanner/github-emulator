# Bug: closing a pull request through the Issues API sends `issues`

Status: fixed 2026-11-08.

## Summary

`PATCH /repos/{o}/{r}/issues/{n}` also closes, reopens and edits a pull
request. On GitHub that is reported as a `pull_request` event, as though the
change had gone through the pulls endpoint. The emulator sent
`issues`/`closed` (or `reopened`, or `edited`) instead. As a result:

- a workflow listening for `pull_request_target: closed` never ran;
- an edit to a pull request's body this way looked like an issue edit.

The labels branch of the same handler already switched to the pull request
event.

Fullsend's behaviour suite closes a pull request with `CloseIssue`. In
Breadboard's behaviour-test T2 tier, `retro-cel-route.feature` ("a close
trigger starts a retro…") failed because the close started no run.

## Fix

The state-change and edit branches dispatch `pull_request_target` with the
pull request payload, on the base ref, when the issue is a pull request.
Webhooks receive it as `pull_request`. A plain issue still sends `issues`.

Tests: `tests/test_webhook_delivery.py`
(`test_closing_a_pull_request_through_the_issues_api_is_a_pull_request_event`).
