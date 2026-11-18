# Bug: an action in a repository on the emulator is fetched upstream

Status: fixed 2026-11-14.

## Summary

The emulator resolved and fetched every `uses: owner/repo@ref` from
github.com. An action pushed to a repository on the emulator therefore
failed with "does not exist upstream". GitHub Enterprise Server resolves an
action against a repository it holds, and goes to github.com only for a
repository it lacks. A test stack that pushes its own actions needs the same.

## Fix

- `actionsdownloadinfo` resolves `owner/repo@ref` against a repository on the
  emulator first. A name the emulator does not hold goes upstream.
- Unlike GHES, a public repository here that lacks the ref also goes
  upstream, so a partial local copy of a public action (one pushed without
  all its tags) still works.
- A private or internal repository here that lacks the ref is unresolvable
  (`404` `UnresolvableActionDownloadInfoException`), and the archive URL for a
  commit it lacks is `404`. Neither is looked up upstream under the same name,
  so a private action's name never resolves to a different upstream
  repository's code.
- As on GitHub, a job may use a repository here as an action only when it is
  public, is the job's own repository, or is private with the same owner as
  the job's repository. GitHub also requires that owner to allow access to
  its private actions; the emulator has no such setting. Another owner's
  private repository is unresolvable (`404`
  `UnresolvableActionDownloadInfoException`) and its name is not looked up
  upstream. The archive URL applies the same rule (`404`), since a job can
  request it directly.
- The name is matched case-insensitively; when two repositories differ only
  in case, the first created is used.
- The archive is built with `git archive`, with one top-level directory as in
  GitHub's archives, and cached apart from upstream archives
  (`action_download_service`).

Tests: `tests/actions/test_emulator_hosted_actions.py`.
