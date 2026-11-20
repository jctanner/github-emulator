# Bug: the connection pool caps concurrent runners

Status: fixed 2026-11-14.

## Summary

A request holds a pooled connection from its first query (usually
authentication) until it ends. The engine used SQLAlchemy's default pool:
5 connections plus 10 overflow. With a dozen runners long-polling for jobs,
posting log lines and updating timelines, all 15 were checked out in a
quarter of the 5 s windows of a 30-minute behaviour run, and requests queued
for a connection. A runner's log post averaged 1.3 s and
`GET /repos/{owner}/{repo}` 0.8 s. The event loop was not the cause: its lag
averaged 9 ms over the same run.

WAL lets readers run side by side, so the pool, not SQLite, was the limit.

## Fix

A file database gets 20 connections plus 40 overflow, set by
`SQLITE_POOL_SIZE` and `SQLITE_MAX_OVERFLOW`. An in-memory database keeps its
single shared connection.

## Evidence

- `tests/test_sqlite_pool_size.py`: the file engine's pool has the new size,
  and the settings change it.
- Fullsend's behaviour suite with 12 runners: 2172 s before, 1710 s after,
  with at most 40 of the 60 connections in use.
