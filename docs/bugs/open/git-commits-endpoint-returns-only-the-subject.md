# `GET /repos/{o}/{r}/git/commits/{sha}` returns only the subject line

## Symptom

The Git Data commit endpoint's `message` is the commit's first line only. A
commit created as `Rework docs\n\nBody line\n\nSigned-off-by: T <t@t>` comes
back with `"message": "Rework docs"`. GitHub returns the full message, body and
trailers included.

## Cause

`src/app/api/git_commits.py` reads the commit with `git log
--format=...%s...`, and `%s` is the subject. The fields are joined with `\x1f`,
so a multi-line `%B` cannot simply replace it without changing how the output
is split.

## Fix direction

Read the message separately (`git log -1 --format=%B <sha>`) or place it last
in the format string and split with a maximum count.

Found while testing `createCommitOnBranch` (2026-10-30); that test reads the
message with `git log` directly for now.
