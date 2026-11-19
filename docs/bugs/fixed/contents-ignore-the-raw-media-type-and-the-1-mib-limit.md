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
  directory and a missing path keep their JSON answers. A raw media type
  listed with `q=0` in `Accept` is not selected, so
  `application/vnd.github.raw;q=0, application/vnd.github+json` gets JSON.
- In JSON, a file of 1,048,576 bytes or more has `encoding` `"none"` and empty
  `content`; the size is checked before encoding, so such a file is not
  base64-encoded. The raw media type still returns every byte.
- The web UI (code browser, README, file editor) reads a file whose JSON has
  `encoding` `"none"` again from the same contents URL with
  `Accept: application/vnd.github.raw`, so it shows and edits the real
  content instead of an empty file (`src/frontend/src/utils/content.ts`).

Tests: `tests/test_contents_media_types.py`,
`src/frontend/src/utils/content.test.ts`.
