# Repository webhooks are never delivered

## Symptom

A repository webhook can be created, listed, updated and deleted, but no
event ever reaches its URL.

On breadboard (2026-10-25), these steps produced no delivery:

1. Created a hook with `events: ["issues"]`, a secret, and the URL
   `http://webhook-sink.ai-pipeline.svc.cluster.local/hooks/github-e2e`.
2. Created an issue in the repository.

`GET /repos/{o}/{r}/hooks/{id}/deliveries` stayed empty, and the hook's
`last_response.status` stayed `unused`. The sink received nothing within
20 s.

## Cause

`src/app/services/webhook_service.py` defines `trigger_webhooks(db,
repo_id, event, payload)`, which finds the matching hooks and calls
`deliver_webhook()`. Nothing calls it. A search of `src/` finds no caller
other than the module itself.

The hook representation also advertises `test_url` (`.../hooks/{id}/tests`)
and `ping_url` (`.../hooks/{id}/pings`). Neither endpoint exists. Org hooks
(`Webhook.org_id`) are not matched by `trigger_webhooks` either.

The delivery path itself works. Called directly in the deployed pod against
the hook above, `deliver_webhook(db, hook, "issues", payload)` posted a
correctly signed request (`X-GitHub-Event`, `X-GitHub-Delivery`,
`X-Hub-Signature-256`). The sink answered 202 with `signature_valid: true`,
and the emulator recorded a successful `WebhookDelivery`.

## Fix

**Where deliveries start.** `workflow_service.dispatch_event` already
receives every repository activity:

- issues and their labels, assignees and milestones;
- issue comments;
- pull requests, including a push that synchronizes one;
- reviews;
- auto-merge;
- GraphQL mutations;
- pushes.

It now calls `webhook_service.schedule_webhooks` first, before the checks
that skip Actions. Webhooks therefore fire when Actions is disabled, or for
a repository with no Git data on disk.

**Event names.** Actions events are mapped to their GitHub webhook names:
`pull_request_target` becomes `pull_request`. `schedule` and
`workflow_call` have no webhook.

**Payloads.** The payload is copied when the event is scheduled, so the
`ref` and `after` fields that Actions adds do not reach hooks. The push
payload now carries the full `repository` and `sender` objects, which
Actions also receives.

**Matching hooks.**

- Matches cover the repository's hooks and its organization's
  (`Webhook.org_id`).
- Matching hooks are found on the caller's session. A repository with no
  subscribed hook therefore starts no task and makes no extra database
  connection.
- `events` and `*` are honored, and inactive hooks receive nothing.

**Background delivery.** Delivery runs as a background task after the
request:

- the HTTP POST has a 10 s timeout and runs with no database connection
  open;
- the delivery rows are then written in one short transaction, retried
  while SQLite reports a lock;
- a hook deleted in the meantime keeps no history.

**`deliver_webhook()` changes:**

- `content_type: form` sends `payload=<json>`, signed over the exact body.
- `insecure_ssl` is honored.
- `X-GitHub-Hook-Installation-Target-Type` and `-ID` are sent.

**Branches.** A push that creates or deletes a branch also sends `create`
or `delete`.

**Hooks API.**

- A new hook is pinged.
- `POST …/hooks/{id}/pings` sends `ping`.
- `POST …/hooks/{id}/tests` sends a `push` of the default branch's head.
  Like GitHub, it sends nothing when the hook is not subscribed to push.
- `last_response` reflects the latest delivery instead of always being
  `unused`.

**A second bug, exposed by the first delivery.** Deleting a hook that had
deliveries failed with `NOT NULL constraint failed:
webhook_deliveries.webhook_id`. `Webhook.deliveries` now cascades the
delete, so a hook's history goes with it.

## Validation

**Tests:** `tests/test_webhook_delivery.py` covers:

- a signed `issues`/`opened` delivery, its recorded delivery, and
  `last_response`;
- event filtering, `*`, and inactive hooks;
- `issue_comment`, and `pull_request` (never `pull_request_target`);
- ping on create, the ping endpoint, and the test endpoint with and without
  a push subscription;
- push with `create` and `delete`;
- a 1.5 s receiver not delaying issue creation;
- form content type and a failed (500) delivery.

`tests/conftest.py` gains an autouse fixture. It records deliveries through
an `httpx.MockTransport` instead of sending them, and waits for them before
the test's database goes away.

`tests/test_webhooks_api.py::test_delete_webhook` now covers the cascade,
because its hook has a ping delivery.

**Full suite:** 750 passed, with
`test_security_headers::test_admin_ui_and_unreserved_admin_path_include_security_headers`
deselected as before; it needs the frontend build.

**Breadboard, 2026-10-25:** the fix was deployed with
`make host-rebuild-github`. A new repository got a hook for `issues`,
`pull_request` and `issue_comment`, with the webhook sink's default sender
token as its secret. Then, through the emulator's API:

- the hook's ping arrived;
- creating an issue returned 201 in 0.068 s, with delivery in the
  background, and `issues`/`opened` arrived;
- a comment delivered `issue_comment`/`created`;
- a pull request delivered `pull_request`/`opened`.

The sink attributed every delivery to `default-sender` by HMAC, and a
consumer long-polling `source=github` received them. The hook's
`last_response` was `{"code": 202, "status": "active", "message": "OK"}`,
and its deliveries list showed all four with status 202.

## Impact (before the fix)

High for any consumer of emulator webhooks. Breadboard's webhook sink is
deployed, and works with the Jira emulator, but receives nothing from the
GitHub emulator until this is fixed.
