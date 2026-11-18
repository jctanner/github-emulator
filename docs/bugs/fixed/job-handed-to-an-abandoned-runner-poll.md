# Bug: a job is handed to a runner poll nobody reads

Status: fixed 2026-11-15.

## Summary

On GitHub, a job handed to a runner reaches that runner, or goes back to
the queue.

`actions/runner` abandons its message poll whenever its status changes and
opens a new one. The emulator kept running the abandoned request, which
could still claim a job. The answer went to nobody, and the job sat in
progress on a runner that never saw it. It hung, or failed with every step
still queued and its log missing. Seen with 12 runners: one job failed
unrun, and no runner's diagnostics ever received its request.

A related case: under load, a runner's first poll timed out on the
runner's side, and the emulator handed a job to it anyway. The runner
reconnected and polled `Online` with no `lastMessageId`. The requeue path
needed a `lastMessageId` naming another message, so after the grace period
the job was failed ("reports idle while holding job N; failing the job"),
although the runner never saw it.

## Fix

- Each poll takes a generation number. Only a runner's newest poll may
  hand out a job. The claim and the settling of the runner's state run
  under a per-runner lock, so an old poll cannot claim between a new
  poll's start and its settling.
- The runner sends `lastMessageId` on every poll and acknowledges each job
  message (`DELETE` of the message) once it has dispatched the job. A job
  the runner holds per the database that it neither acknowledged nor named
  as its last message was never received: it goes back to the queue
  instead of failing.
- A poll with no `lastMessageId` counts as "never received" when the job
  has not started a step. A runner restarted mid-job (no `lastMessageId`,
  steps started) still fails it.
- A job handed out less than 5 s ago is never requeued as unreceived: a
  request the runner sent before the hand-out can arrive after it, with
  an older `lastMessageId`. Every pass of the long poll re-checks, so a
  lost hand-out is still requeued within seconds.
- The requeue is a conditional `UPDATE` that matches only a job still in
  progress on that runner, so a report that committed meanwhile wins.
  Acknowledgements are kept for good (job ids are never reused).
- The poll commits after settling the runner and before the claim: under
  WAL, a write in a transaction that began by reading fails at once when
  another writer committed meanwhile.
- Hand-outs, requeues, acknowledgements, cancellations and reports are
  logged under `github_emulator.runner_broker`. `app.main` now gives the
  `github_emulator` loggers a handler (`GITHUB_EMULATOR_LOG_LEVEL`, `INFO`
  by default); before, their `INFO` lines never reached the server log.

Tests: `tests/actions/test_runner_lost_handout.py`.

- A job its runner never received is requeued, not failed.
- A job handed to an abandoned first poll is requeued.
- A newer poll replaces an older one before the older can claim.
- Stress: 4 runners, 12 jobs, half the polls abandoned with the server
  left to answer them. Every job reaches a live poll exactly once and
  completes.

## Known flake

`test_a_newer_poll_replaces_an_older_one_before_it_can_claim` can fail once
with a 503 from the test database's 0.1 s lock timeout. It passes on rerun.
The production lock timeout is much longer.
