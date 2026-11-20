# Bug: contents update ignores the `sha` precondition

Status: fixed 2026-11-14.

## Summary

On GitHub, `PUT /repos/{owner}/{repo}/contents/{path}` on a file that already
exists needs `sha`, the file's current blob sha. It works as a
compare-and-swap:

- a stale `sha` is `409 Conflict`, `<path> does not match <sha>`;
- no `sha` is `422`, `Invalid request. "sha" wasn't supplied.`;
- with `branch`, the `sha` is compared with that branch's copy of the file.

The emulator ignored `sha`. A stale or missing `sha` answered 200 and
overwrote the file. A client that uses a file as a lock (read the sha, PUT
with it, treat 409 or 422 as "someone else won") saw both writers win. Fullsend's
behaviour tests rely on this.

Creating a path that does not exist yet with a `sha` that matches nothing is
still a create (201). GitHub does not check `sha` on a create, and the
emulator now does the same.

`DELETE .../contents/{path}` already checked `sha` (409 on a mismatch, 422 when
it is missing), on the named branch too. It is unchanged; the new tests cover
the branch case.

## Fix

`create_or_update_file` in `src/app/api/contents.py`:

- reads the branch head with `rev-parse --verify refs/heads/<branch>` (as
  `delete_file` does), and the file's current blob from that commit;
- on an existing file, answers 422 without `sha` and 409 when `sha` differs,
  before writing anything;
- moves the branch with `git update-ref <ref> <new> <old>` (an empty old value
  for a new branch). If another write moved the branch after the check, this
  write fails with 409 instead of overwriting, so of two concurrent writers
  with the same `sha` exactly one wins.

## Tests

`tests/test_contents_api.py`:

- `test_update_file_sha_is_a_compare_and_swap`: correct sha 200, stale sha
  409, missing sha 422, file unchanged after the refusals.
- `test_concurrent_updates_with_the_same_sha_have_one_winner`: two parallel
  PUTs with the same sha give one 200 and one 409.
- `test_update_file_sha_is_checked_on_the_named_branch`: with `branch`, the
  sha is compared with that branch's file.
- `test_delete_file_sha_is_checked_on_the_named_branch`: DELETE with `branch`
  gives 409 on a mismatch and 422 without a sha.
- `test_create_file_ignores_an_unmatched_sha`: a create with a sha is 201.
- `test_update_file` now sends the sha, as do the update helpers in
  `tests/test_raw_content.py` and `tests/test_branch_registration.py`.
