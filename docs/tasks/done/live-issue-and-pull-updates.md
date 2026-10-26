# Live issue and pull-request updates

## Goal

An open issue or pull-request page shows changes made by anyone else
(comments, closes, merges, reviews, labels) without a manual refresh.

## Implementation

- `app.services.live_updates` hooks the SQLAlchemy `Session` instead of
  individual routes, so every writer is covered: REST, GraphQL, the web UI,
  Actions and Fullsend.
  - `after_flush` maps each new, changed or deleted row to a topic. Issues,
    comments, events, labels and assignees map to `("issue", id)`; pull
    requests also map to `("pull", id)`; reviews and review comments map to
    `("pull", pull_request_id)`.
  - `do_orm_execute` covers bare bulk `DELETE`/`UPDATE` on issue labels and
    assignees by reading the issue id from the WHERE clause.
  - `after_commit` wakes the subscribers, through `call_soon_threadsafe`.
    A rollback announces nothing.
- `GET /api/_ui/repos/{owner}/{repo}/issues/{number}/live` streams
  server-sent events: `data: changed` on a change, a heartbeat comment every
  20 seconds, and `retry: 2000`.
  - It applies the same visibility check as the navigation endpoint.
  - It closes its database session before streaming, so an open page never
    holds a SQLite read transaction.
- Subscribers live in this one process, which is how the emulator is
  deployed (one uvicorn process, one replica). A second worker would need a
  shared channel instead.
- uvicorn runs with `--timeout-graceful-shutdown 5`, so open streams cannot
  hold up a rollout. Browsers reconnect on their own.
- In the frontend, `useLiveUpdates` opens an `EventSource` on the issue page
  and the pull-request Conversation, Commits and Files changed tabs.
  - It collapses a burst of changes into one background reload, using the
    in-place reload from `web-ui-reload-in-place.md`.
  - It reloads once after reconnecting, because changes made while the
    stream was down were not announced.
  - The issue and Conversation pages also refresh the repository tab counts.

## Verification

- `tests/test_live_updates.py` covers:
  - a comment wakes only its own issue, and so does a close;
  - a review wakes its pull request;
  - a bare bulk delete wakes the issue (this test fails with the bulk hook
    disabled);
  - a rollback wakes nobody;
  - the endpoint returns 404 for an unknown issue.
- `src/hooks/useLiveUpdates.test.ts` covers burst coalescing,
  reload-on-reconnect and closing the stream.
- Full backend suite: 758 passed. Frontend: 72 passed, with lint and
  typecheck clean.
- Live on the Breadboard cluster:
  - through the host proxy and Caddy, `data: changed` arrived 75 ms after
    another client posted a comment;
  - in Chrome, a page open as `reviewer-ui` showed an admin's API comment and
    close on issue #2, then a comment and merge on pull #1, without a reload
    and in the same document.
