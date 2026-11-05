# Subdirectory README not shown in the code browser

## Status: Fixed

## Summary

Opening a folder in the web UI's code browser (for example
`/ui/admin/breadboard/tree/initiative-tests/var/demos/rhaistrat-initiative-tests`)
listed its files but did not render its `README.md` below the listing. GitHub
does, for every directory, not only the repository root.

## Cause

- `CodeBrowserPage.tsx` fetched a README only when `path` was empty.
- The API implemented only `GET /repos/{owner}/{repo}/readme` (the root).
  GitHub's REST API also has `GET /repos/{owner}/{repo}/readme/{dir}`.

## Fix

- `src/app/api/contents.py`: `GET /repos/{owner}/{repo}/readme/{dir}`,
  sharing one lookup with the root route (same README file names, `ref`
  support, `path` set to the file's full path, 404 when the directory has
  none or the path has dot segments).
- `CodeBrowserPage.tsx`: a directory requests its own README (the root's
  route at the root) and shows it only when the response is a file.
- `src/frontend/src/api/schema.d.ts` regenerated (`make frontend-api`).
- Tests: `tests/test_contents_api.py::test_get_directory_readme`, and
  `CodeBrowserPage.test.tsx` "shows a folder's own README below its listing".
