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

1. Call `trigger_webhooks` from the event sources, starting with the
   events breadboard consumers need:
   - `issues`;
   - `issue_comment`;
   - `pull_request`;
   - `pull_request_review`;
   - `push`, from Git receive-pack;
   - `create` and `delete`, for refs.
2. Build the payloads with the same serializers as the REST responses, plus
   `action`, `repository`, `sender`, and `organization` where it applies.
3. Match org hooks for repositories owned by the org.
4. Add `POST /hooks/{id}/pings`, which sends a `ping` event with `zen` and
   `hook_id`, and `POST /hooks/{id}/tests`, which sends a `push` for the
   latest commit.
5. Do not deliver inline. `deliver_webhook` waits up to 30 s on the
   receiver, inside the request that caused the event, so a slow receiver
   would stall the API. Queue the delivery after commit, as the Jira
   emulator's outbox worker does, or use a background task.

## Validation needed

- [ ] Creating an issue delivers `issues`/`opened` to a subscribed hook,
      with a valid `X-Hub-Signature-256`, and records a delivery.
- [ ] Hooks not subscribed to the event receive nothing; a `*` hook
      receives everything.
- [ ] Ping and test endpoints work.
- [ ] A receiver that never answers does not slow the triggering request.
- [ ] Breadboard end to end: an issue in the emulator reaches a
      `webhook-sink` consumer.

## Impact

High for any consumer of emulator webhooks. Breadboard's webhook sink is
deployed, and works with the Jira emulator, but receives nothing from the
GitHub emulator until this is fixed.
