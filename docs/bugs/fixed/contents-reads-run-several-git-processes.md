# Bug: a contents read runs two or three git processes

Status: fixed 2026-11-16.

## Summary

`GET /repos/{owner}/{repo}/contents/{path}` ran three git processes
(`rev-parse`, `cat-file -t`, then `cat-file blob` or `ls-tree`). The README
lookup ran two per candidate name, and the raw route two. A client that loads
a tree of configuration files makes about a thousand of these calls per job,
and on a CPU-bound machine starting processes is most of each call's cost.

A directory entry with a non-ASCII name was also listed with git's quoting
(`"\303\251.md"`) instead of its name.

## Fix

Each call runs one `git cat-file --batch` that resolves `<ref>:<path>` and
returns the object (`bare_repo.read_object`). A directory is listed from the
tree object itself (`bare_repo.parse_tree`), so names are no longer quoted.
The raw route reads `<ref>:<path>` the same way and answers 404 unless the
object is a file, as GitHub's raw host does for a directory.

Local timing, 60 calls each, median before / after:

| Load | Contents (file) | Contents (directory) | Raw |
|---|---|---|---|
| idle | 30.7 / 12.1 ms | 30.2 / 12.0 ms | 21.7 / 12.2 ms |
| all 10 cores busy | 112 / 50 ms | 114 / 50 ms | 89 / 50 ms |

Responses are otherwise unchanged. `tests/fixtures/contents_responses.json`
pins 28 of them, recorded before the change: file, executable, binary,
directory, root, symlink, submodule, missing path and bad ref, with the ref as
a branch, a tag, an annotated tag and a commit sha.

Tests: `tests/test_contents_responses_pinned.py`.
