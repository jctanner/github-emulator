# Bug: an API commit answers 503 after the branch already moved

Status: fixed 2026-11-15.

## Summary

The contents API (`PUT` and `DELETE /repos/{owner}/{repo}/contents/{path}`)
and GraphQL `createCommitOnBranch` move the git ref first, then record the
branch and `pushed_at` in SQLite. With many runners and clients writing at
once (Fullsend's behaviour tests: 6 workers, 12 runners), that second write
failed with `database is locked`:

- `createCommitOnBranch` returned an error, but the commit was on the branch;
- `PUT contents` answered 503, the commit was on the branch, and the
  client's retry committed the change a second time (with a sha check, it
  got a conflict instead).

`POST` and `PATCH /repos/{owner}/{repo}/git/refs` had the same order: a
lock failure answered 503 with the ref already moved, so the client saw a
failure for a change that had been made.

The cause of the lock is not yet confirmed. The database driver starts a
transaction only at the first write, so the instant snapshot conflict
(below) is unlikely; the more likely cause is another request holding
SQLite's writer lock past the busy timeout (then 5 s). The writer-lock logging
(`app/database_diag.py`) is there to name it. The fix below holds either
way.

## Fix

`record_moved_branch` in `src/app/services/branch_service.py`, used by the
contents and `createCommitOnBranch` routes, and `replay_ref_record` in the
same file, used by the git/refs routes, both:

1. Replay the database write on a lock (`SQLITE_WRITE_RETRY_ATTEMPTS` is
   now 5). `record_moved_branch` first ends the read transaction and writes
   through `commit_with_sqlite_retry`; `replay_ref_record` stages the
   route's write uncommitted and commits it through the same helper, which
   stages it again after a rollback. Retries stop once a busy timeout's
   worth of time has passed. Auto-merge re-evaluation runs after the
   git/refs write is committed, outside the replay.
2. Never record an older sha over a newer one. Each attempt, the first
   included, takes the repository's write lock (a no-op UPDATE of its
   row) and only then reads the ref (`commit_if_ref_unmoved` in
   `branch_service.py`). A writer that moved the ref later has either
   committed already (the ref has moved, so this request stages nothing)
   or waits for this commit and writes over it. The branch record, the
   git/refs record and the open pull requests' heads all go through it.
   An API commit the branch has moved past still raises its push
   activity, but leaves the heads (and their synchronize activity) to the
   newer push. If the heads' own update meets a lock that persists, the
   already committed write still answers 200 and the heads wait for the
   next push; that is logged.
   API writes to one repository also run one at a time: each route takes the
   repository's write lock (`app/services/repository_lock.py`) before it
   reads the ref it will move, and holds it until the response is sent, so
   no other API writer's ref move, record or rollback lands in between.
   Git pushes take their own path and keep only the ordering rule above.
3. If the lock persists, move the ref back before answering 503, so a
   retry finds the repository as it was. This happens only while the ref
   is still at the new commit: a ref another writer has moved since keeps
   its commit. A created ref is deleted, and an existing ref that a create
   moved is put back.

## Evidence

- Plain `sqlite3`, WAL, explicit transactions: connection A reads in a
  transaction, B commits an update, A's update fails with `database is
  locked` after 0.00 s (`SQLITE_BUSY_SNAPSHOT`). With the driver's default
  handling a read does not open a transaction, so this is one possible
  cause, not the observed one.
- `tests/test_api_commit_lock_replay.py`: a brief lock is replayed through
  (200); a held lock answers 503 with the branch unchanged, and the same
  request then succeeds; `createCommitOnBranch` the same. All three fail on
  the previous code. Two more cover `POST` and `PATCH git/refs`.
- Fullsend behaviour run with 6 workers and 12 runners: 12 `database is
  locked` errors, all from `createCommitOnBranch`, and one `PUT contents` 503
  followed by a failed retry.
