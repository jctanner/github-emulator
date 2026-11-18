# Bug: contents ignore the raw media type and the 1 MiB limit

Status: fixed 2026-11-16.

## Summary

`GET /repos/{owner}/{repo}/contents/{path}` and `GET .../readme` ignored the
`Accept` header. With `application/vnd.github.raw` (also `.v3.raw` and
`.raw+json`), GitHub answers a file with its bytes and that media type as the
`Content-Type`. A directory still gets the JSON listing, and a missing path
the JSON 404. A client that asks for the raw media type fell back to decoding
the JSON object.

In the JSON media type, GitHub leaves out the content of a file of 1 MiB
(1,048,576 bytes) or more: `encoding` is `"none"` and `content` is `""`, with
the real size and sha. 1,048,575 bytes is the largest file it returns in
base64. The emulator returned every file base64-encoded, so a large-file read
that fails on GitHub passed here.

## Fix

- Both routes answer the three raw media types with the file's bytes. A
  directory and a missing path keep their JSON answers.
- In JSON, a file of 1,048,576 bytes or more has `encoding` `"none"` and empty
  `content`. The raw media type still returns every byte.

Tests: `tests/test_contents_media_types.py`.
