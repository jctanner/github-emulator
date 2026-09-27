# Bug: a push processed after its pull request opens fires `synchronize`

## Summary

Receive-pack hands the push to `process_push_event` as a background task.
If a pull request is opened on that branch before the task runs, the task
finds it open and dispatches `pull_request_target: synchronize` for a push
that predates the pull request. The workflow then runs twice for one
event: `opened` and a spurious `synchronize`.

Seen twice on 2026-09-27 while the emulator was stalled by a tracemalloc
experiment, which stretched the window from milliseconds to minutes:

| push | pull request opened | synchronize run | delay |
| --- | --- | --- | --- |
| ~18:20:20 `review-phase2-…` | 18:20:33 (#131, run 1603 `opened`) | 18:22:43 (run 1604) | ~2 min |
| ~18:54:20 `review-phase2b-…` | 18:54:26 (#133, run 1608 `opened`) | 18:55:12 (run 1609, for #132 on the same branch) | ~1 min |

Each duplicate would have been a second paid review had it not been
cancelled by hand. GitHub fires `synchronize` only for a head change that
happens after the pull request exists.

## Fix

Record the head sha the push moved *to* and dispatch `synchronize` only
for pull requests whose stored `head_sha` differs from it and which were
created before the push was received - or process the push synchronously
before the receive-pack response is sent, which is what makes the branch
visible to the client anyway.

## Related, observed in the same session

Cancelling a run does not stop the runner: `runner.py` kept executing the
cancelled review job for fifteen minutes, appended logs to it, and
completed it, because it never re-reads the job's status. The emulator
should refuse log appends and completions for a cancelled job with a
status the runner can act on, and the runner should poll for cancellation
between steps. Recorded here rather than as its own file because the fix
touches both sides of the same protocol.
