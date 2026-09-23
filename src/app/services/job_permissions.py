"""Enforce a workflow job's declared ``permissions:`` on its job token.

GitHub scopes the automatic ``GITHUB_TOKEN`` to the permissions a job
declares. The semantics this mirrors, per the Actions reference:

- Fourteen scopes, each ``read``, ``write``, or ``none``.
- **Declaring any permission sets every unspecified scope to ``none``.**
  This is the important one: a job asking for ``issues: write`` thereby gives
  up pull-request access entirely.
- ``metadata`` is always readable.
- ``permissions: {}`` grants nothing; ``read-all`` and ``write-all`` are
  shorthands for every scope at that level.
- A refusal is ``403 Resource not accessible by integration``.

Enforcement applies only to requests carrying a job token. Personal access
tokens, installation tokens, and browser sessions are untouched, which keeps
the blast radius to workflow runs.

Two deliberate deviations, both recorded in the Fullsend conformance plan:

1. A job that declares no permissions at all is treated as permissive. On
   GitHub the default comes from repository or organisation settings and can
   be either permissive or restricted; the emulator has no such setting, and
   defaulting to restricted would break existing fixtures that declare
   nothing.
2. An endpoint this module does not map is allowed for reads and denied for
   writes. Denying unmapped reads would be more faithful but turns any gap in
   the map into a confusing mid-run failure, whereas an unmapped write is
   logged so the gap is visible.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("github_emulator.actions.permissions")

READ = "read"
WRITE = "write"
NONE = "none"

# The scope that governs each path segment under /repos/{owner}/{repo}/.
# "metadata" is always readable, so segments mapped to it never deny a read.
_SCOPE_BY_SEGMENT = {
    "issues": "issues",
    "labels": "issues",
    "milestones": "issues",
    "pulls": "pull-requests",
    "contents": "contents",
    "git": "contents",
    "commits": "contents",
    "branches": "contents",
    "releases": "contents",
    "tags": "contents",
    "merges": "contents",
    "actions": "actions",
    "check-runs": "checks",
    "check-suites": "checks",
    "statuses": "statuses",
    "deployments": "deployments",
    "packages": "packages",
    "pages": "pages",
    "discussions": "discussions",
    "hooks": "administration",
    "keys": "administration",
    "collaborators": "administration",
    # Forking creates a repository from this one's contents. GitHub's
    # fine-grained equivalent is a Contents write.
    "forks": "contents",
}

# Segments whose reads are metadata-level even though their writes need a
# stronger scope. Reading a collaborator's permission is the case that matters
# here: Fullsend's dispatch authorizes an event with exactly that call while
# declaring only contents/issues/pull-requests, and it works on GitHub, so the
# read cannot require administration.
_METADATA_READ_SEGMENTS = {"collaborators"}

_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def normalise_permissions(declared: dict | str | None) -> dict[str, str] | None:
    """Return the effective scope map, or None when the job declared nothing.

    None means "no permissions block", which this emulator treats as
    permissive. An empty mapping means ``permissions: {}``, which grants
    nothing beyond metadata.
    """
    if declared is None:
        return None
    if isinstance(declared, str):
        value = declared.strip().lower()
        if value == "read-all":
            return {scope: READ for scope in set(_SCOPE_BY_SEGMENT.values())}
        if value == "write-all":
            return {scope: WRITE for scope in set(_SCOPE_BY_SEGMENT.values())}
        return {}
    if not isinstance(declared, dict):
        return None
    if not declared:
        return {}
    return {str(k).lower(): str(v).lower() for k, v in declared.items()}


def _scope_for(path: str) -> tuple[str | None, bool]:
    """Map a request path to its governing scope.

    Returns ``(scope, mapped)``. ``scope`` is None for the repository root and
    other metadata-only paths; ``mapped`` is False when the path is outside
    the map entirely.
    """
    trimmed = path.split("?", 1)[0].strip("/")
    for prefix in ("api/v3/", "api/v3", "v3/"):
        if trimmed.startswith(prefix):
            trimmed = trimmed[len(prefix):]
            break
    parts = [p for p in trimmed.split("/") if p]
    if len(parts) >= 3 and parts[0] == "repos":
        if len(parts) == 3:
            # /repos/{owner}/{repo} itself is metadata.
            return None, True
        segment = parts[3].lower()
        if segment in _SCOPE_BY_SEGMENT:
            return _SCOPE_BY_SEGMENT[segment], True
        return None, False

    return None, False


def _is_metadata_read(method: str, path: str) -> bool:
    """Whether this is a read that metadata alone permits."""
    if method.upper() not in _READ_METHODS:
        return False
    trimmed = path.split("?", 1)[0].strip("/")
    for prefix in ("api/v3/", "api/v3", "v3/"):
        if trimmed.startswith(prefix):
            trimmed = trimmed[len(prefix):]
            break
    parts = [p for p in trimmed.split("/") if p]
    return (
        len(parts) >= 4
        and parts[0] == "repos"
        and parts[3].lower() in _METADATA_READ_SEGMENTS
    )


# What GitHub grants when a repository's default is "read". Not a blanket
# read of everything: the restricted default is contents and packages, plus
# the metadata every token carries.
_RESTRICTED_DEFAULT = {"contents": READ, "packages": READ, "metadata": READ}


def default_permissions_for(repo_default: str | None) -> dict[str, str] | None:
    """Translate a repository's default_workflow_permissions into scopes.

    ``None`` means permissive, which is what a repository set to "write" —
    GitHub's own default — grants. "read" is the restricted setting, and a job
    declaring nothing on such a repository must not be able to write.
    """
    if str(repo_default or "write").strip().lower() == "read":
        return dict(_RESTRICTED_DEFAULT)
    return None


def check(
    method: str,
    path: str,
    declared: dict | str | None,
    repo_default: str | None = None,
) -> str | None:
    """Return a refusal reason, or None when the request is permitted."""
    permissions = normalise_permissions(declared)
    if permissions is None:
        # No permissions block: the repository's default decides. It is
        # permissive on a repository set to "write", which is GitHub's own
        # default, and restricted to reads on one set to "read".
        inherited = default_permissions_for(repo_default)
        if inherited is not None:
            return check(method, path, inherited)
        # Permissive, as described in the module docstring.
        return None

    if _is_metadata_read(method, path):
        return None

    scope, mapped = _scope_for(path)
    is_read = method.upper() in _READ_METHODS

    if scope is None:
        if mapped:
            # Metadata is always readable; metadata has no write form.
            return None if is_read else "metadata is read-only"
        if is_read:
            return None
        logger.warning(
            "Unmapped write path %s %s denied for a job token; add it to "
            "_SCOPE_BY_SEGMENT if a scope governs it",
            method, path,
        )
        return "endpoint is not covered by any granted permission"

    granted = permissions.get(scope, NONE)
    if granted == WRITE:
        return None
    if granted == READ and is_read:
        return None
    if granted in (NONE, "") or granted not in (READ, WRITE):
        return f"the job's permissions do not include {scope}"
    return f"the job's {scope} permission is {granted}, which does not allow this"
