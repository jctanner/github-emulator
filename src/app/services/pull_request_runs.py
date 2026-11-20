"""``pull_request`` workflow runs, beside ``pull_request_target``.

On GitHub one pull request activity (opened, labeled, synchronize, ...) runs
both ``pull_request_target`` and ``pull_request`` workflows. They differ in
what they run:

- ``pull_request_target`` runs the base branch's workflow, with the base
  repository's token and secrets.
- ``pull_request`` runs the workflow in the pull request's merge commit,
  ``refs/pull/{number}/merge`` (the head merged into the base branch), with
  ``GITHUB_SHA`` that merge commit and ``GITHUB_REF`` that ref. A pull request
  from a fork gets a read-only ``GITHUB_TOKEN``, no secrets and no OIDC token.
  A pull request that does not merge cleanly has no merge commit, and GitHub
  runs no ``pull_request`` workflow for it.

Every activity here is raised once, as ``pull_request_target``, by
``dispatch_event``; it calls :func:`dispatch_pull_request` for the
``pull_request`` side, so both run for every activity and the
``pull_request_target`` behaviour is unchanged.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os

from app.services.cross_repo_pulls import pull_ref

logger = logging.getLogger(__name__)

# GitHub names itself as the author and committer of a merge ref.
_MERGE_IDENTITY = {
    "GIT_AUTHOR_NAME": "GitHub",
    "GIT_AUTHOR_EMAIL": "noreply@github.com",
    "GIT_COMMITTER_NAME": "GitHub",
    "GIT_COMMITTER_EMAIL": "noreply@github.com",
}


def merge_ref(number: int) -> str:
    return f"refs/pull/{number}/merge"


async def _git(repo_path: str, *args: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **_MERGE_IDENTITY, "GIT_DIR": repo_path},
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode().strip(), err.decode().strip()


async def _commit_exists(repo_path: str, sha: str) -> bool:
    code, _, _ = await _git(repo_path, "cat-file", "-e", f"{sha}^{{commit}}")
    return code == 0


async def update_pull_request_refs(
    repo_path: str, number: int, head_sha: str, base_ref: str,
) -> str | None:
    """Point ``refs/pull/{number}/head`` at the head and rebuild
    ``refs/pull/{number}/merge``: the head merged into the base branch's
    current tip, as GitHub computes it.

    Returns the merge commit, or None when there is none: the head or base is
    missing, or the two do not merge cleanly (the stale merge ref is then
    removed, as GitHub drops it for a conflicting pull request).
    """
    if not head_sha or not await _commit_exists(repo_path, head_sha):
        return None
    # A cross-repository head is already copied here as refs/pull/N/head
    # (cross_repo_pulls.sync_head_ref); a same-repository one is set now.
    await _git(repo_path, "update-ref", pull_ref(number), head_sha)

    base_name = base_ref if base_ref.startswith("refs/") else f"refs/heads/{base_ref}"
    code, base_sha, _ = await _git(repo_path, "rev-parse", "--verify", f"{base_name}^{{commit}}")
    if code != 0 or not base_sha:
        return None

    code, tree, _ = await _git(repo_path, "merge-tree", "--write-tree", "--no-messages", base_sha, head_sha)
    if code != 0 or not tree:
        # 1: conflicts. Anything else: no merge base, or the merge failed.
        await _git(repo_path, "update-ref", "-d", merge_ref(number))
        return None
    tree = tree.splitlines()[0].strip()
    code, merge_sha, err = await _git(
        repo_path, "commit-tree", tree, "-p", base_sha, "-p", head_sha,
        "-m", f"Merge {head_sha} into {base_sha}",
    )
    if code != 0 or not merge_sha:
        logger.warning("Could not write the merge commit of pull request %s: %s", number, err)
        return None
    await _git(repo_path, "update-ref", merge_ref(number), merge_sha)
    return merge_sha


def pull_request_head_is_fork(payload: dict) -> bool:
    """Whether the pull request in ``payload`` comes from another repository:
    its ``head.repo.id`` differs from the repository's.

    Every payload the emulator builds carries both ids (a head repository it
    cannot load is still named by id, and marked a fork). A payload without
    them is not a pull request activity's, so it is not treated as a fork.
    """
    pull_request = (payload or {}).get("pull_request") or {}
    head_id = ((pull_request.get("head") or {}).get("repo") or {}).get("id")
    base_id = ((payload or {}).get("repository") or {}).get("id")
    if base_id is None:
        base_id = ((pull_request.get("base") or {}).get("repo") or {}).get("id")
    return head_id is not None and base_id is not None and str(head_id) != str(base_id)


def is_fork_pull_request_run(run) -> bool:
    """A ``pull_request`` run for a fork's pull request: GitHub gives it a
    read-only token, no secrets and no OIDC token."""
    if run is None or getattr(run, "event", None) != "pull_request":
        return False
    return pull_request_head_is_fork(run.trigger_payload or {})


async def dispatch_pull_request(db, repository, actor, action: str, payload: dict) -> list:
    """Run the ``pull_request`` workflows for one pull request activity.

    ``payload`` is the activity's ``pull_request_target`` payload; it is
    copied, not changed.
    """
    from app.services.workflow_service import dispatch_event

    pull_request = payload.get("pull_request") or {}
    number = pull_request.get("number")
    head = pull_request.get("head") or {}
    base = pull_request.get("base") or {}
    if not number or not repository.disk_path:
        return []
    merge_sha = await update_pull_request_refs(
        repository.disk_path, int(number), head.get("sha") or "", base.get("ref") or "",
    )
    if merge_sha is None:
        logger.info(
            "No merge commit for %s#%s: no pull_request workflows run for %s",
            repository.full_name, number, action,
        )
        return []
    event_payload = copy.deepcopy(payload)
    event_payload["ref"] = merge_ref(int(number))
    event_payload["after"] = merge_sha
    return await dispatch_event(
        db, repository, actor, "pull_request", action, event_payload,
        ref=merge_ref(int(number)), sha=merge_sha, _pull_request_side=True,
    )
