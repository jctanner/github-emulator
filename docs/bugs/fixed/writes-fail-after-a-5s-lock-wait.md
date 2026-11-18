# Bug: writes fail with 503 after a 5 s wait for SQLite's writer lock

Status: fixed 2026-11-15.

## Summary

SQLite allows one writer at a time; other writers wait up to the busy
timeout, then fail with `database is locked`, which the emulator answers as
503. The timeout was 5 s.

In Fullsend's behaviour tests (3 shards, each 6 workers and 12 runners on a
4-vCPU VM with load up to 25), 97 writes failed this way. Every failure had
waited 5019–5368 ms (median 5070 ms), so none was an instant conflict. The
runner retried its own calls (timeline updates, job polls, plan events),
but clients saw 503s on comments, labels, GraphQL, installation tokens and
git refs, and a write that had already moved a git ref left the client's
retry with a conflict.

## Fix

`SQLITE_BUSY_TIMEOUT_MS` (`GITHUB_EMULATOR_SQLITE_BUSY_TIMEOUT_MS`) now
defaults to 30000. The longest lock wait plus hold observed was 8.5 s, so a
write queues instead of failing. Setting the variable restores a shorter
timeout.

## Evidence

- Fullsend behaviour tests on the emulator, writer-lock logs of 3 shards:
  97 "database is locked after N ms" lines, N between 5019 and 5368.
