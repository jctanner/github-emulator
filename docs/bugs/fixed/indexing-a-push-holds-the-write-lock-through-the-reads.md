# Bug: indexing a pushed repository holds the write lock through the reads

Status: fixed 2026-11-14.

## Summary

After a push, the search indexer deleted the repository's old index rows
first, then read every file in the tree with its own git subprocess. The
delete took SQLite's single writer lock, so the lock was held for the whole
tree walk. After a large push, other writes in that window waited past the
busy timeout and were answered 503 "Database is busy".

## Fix

`src/app/services/index_service.py` ends any write the caller has open,
reads the tree and the log first, and then replaces the index in one short
transaction.

## Evidence

- `tests/test_index_write_lock.py`: a second connection writes while the
  indexer reads the tree. It fails with "database is locked" on the previous
  code and passes now.
