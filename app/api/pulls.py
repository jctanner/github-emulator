"""Pull request endpoints -- list, create, get, update, merge PRs."""

import asyncio
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, func as sa_func
from sqlalchemy.orm import selectinload

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.git.bare_repo import get_compare_diff, get_log, normalize_branch_ref
from app.models.issue import Issue
from app.models.pull_request import PullRequest
from app.models.repository import Repository
from app.schemas.user import SimpleUser, _fmt_dt, _make_node_id

router = APIRouter(tags=["pulls"])

BASE = settings.BASE_URL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pr_query():
    """Return a base select for PullRequest with eager-loaded relationships."""
    return (
        select(PullRequest)
        .options(
            selectinload(PullRequest.issue).selectinload(Issue.user),
            selectinload(PullRequest.repository).selectinload(Repository.owner),
            selectinload(PullRequest.head_repository),
            selectinload(PullRequest.merged_by),
        )
    )


def _pr_json(pr: PullRequest, base_url: str) -> dict:
    """Build a GitHub-compatible pull-request JSON object."""
    api = f"{base_url}/api/v3"
    issue = pr.issue
    repo = pr.repository
    owner_login = repo.owner.login if repo and repo.owner else "unknown"
    repo_name = repo.name if repo else "unknown"
    repo_full = f"{owner_login}/{repo_name}"
    pr_url = f"{api}/repos/{repo_full}/pulls/{issue.number}"
    issue_url = f"{api}/repos/{repo_full}/issues/{issue.number}"

    user_simple = SimpleUser.from_db(issue.user, base_url).model_dump() if issue.user else None
    merged_by = SimpleUser.from_db(pr.merged_by, base_url).model_dump() if pr.merged_by else None

    head_repo = pr.head_repository or repo
    head_owner = head_repo.owner if head_repo else None
    head_ref = getattr(pr, "resolved_head_ref", pr.head_ref)
    base_ref = getattr(pr, "resolved_base_ref", pr.base_ref)
    head_sha = getattr(pr, "resolved_head_sha", pr.head_sha)
    base_sha = getattr(pr, "resolved_base_sha", pr.base_sha)
    head_label = getattr(
        pr,
        "resolved_head_label",
        f"{head_owner.login if head_owner else owner_login}:{head_ref}",
    )

    return {
        "url": pr_url,
        "id": pr.id,
        "node_id": _make_node_id("PullRequest", pr.id),
        "html_url": f"{base_url}/{repo_full}/pull/{issue.number}",
        "diff_url": f"{base_url}/{repo_full}/pull/{issue.number}.diff",
        "patch_url": f"{base_url}/{repo_full}/pull/{issue.number}.patch",
        "issue_url": issue_url,
        "number": issue.number,
        "state": issue.state,
        "locked": issue.locked,
        "title": issue.title,
        "user": user_simple,
        "body": issue.body,
        "created_at": _fmt_dt(issue.created_at),
        "updated_at": _fmt_dt(issue.updated_at),
        "closed_at": _fmt_dt(issue.closed_at),
        "merged_at": _fmt_dt(pr.merged_at),
        "merge_commit_sha": pr.merge_commit_sha,
        "assignee": None,
        "assignees": [],
        "requested_reviewers": [],
        "requested_teams": [],
        "labels": [],
        "milestone": None,
        "draft": pr.draft,
        "commits_url": f"{pr_url}/commits",
        "review_comments_url": f"{pr_url}/comments",
        "review_comment_url": f"{api}/repos/{repo_full}/pulls/comments{{/number}}",
        "comments_url": f"{issue_url}/comments",
        "statuses_url": f"{api}/repos/{repo_full}/statuses/{head_sha}",
        "head": {
            "label": head_label,
            "ref": head_ref,
            "sha": head_sha,
            "user": SimpleUser.from_db(head_owner, base_url).model_dump() if head_owner else user_simple,
            "repo": None,  # Simplified
        },
        "base": {
            "label": f"{owner_login}:{base_ref}",
            "ref": base_ref,
            "sha": base_sha,
            "user": SimpleUser.from_db(repo.owner, base_url).model_dump() if repo and repo.owner else None,
            "repo": None,  # Simplified
        },
        "_links": {
            "self": {"href": pr_url},
            "html": {"href": f"{base_url}/{repo_full}/pull/{issue.number}"},
            "issue": {"href": issue_url},
            "comments": {"href": f"{issue_url}/comments"},
            "review_comments": {"href": f"{pr_url}/comments"},
            "review_comment": {"href": f"{api}/repos/{repo_full}/pulls/comments{{/number}}"},
            "commits": {"href": f"{pr_url}/commits"},
            "statuses": {"href": f"{api}/repos/{repo_full}/statuses/{head_sha}"},
        },
        "author_association": "OWNER",
        "merged": pr.merged,
        "mergeable": pr.mergeable,
        "merged_by": merged_by,
        "comments": 0,
        "review_comments": 0,
        "maintainer_can_modify": True,
        "commits": 1,
        "additions": 0,
        "deletions": 0,
        "changed_files": 0,
    }


async def _attach_resolved_refs(pr: PullRequest, repository: Repository) -> None:
    """Attach normalized refs and SHAs for response and git operations."""
    owner_login = repository.owner.login if repository.owner else None
    head_ref = pr.head_ref
    head_sha = pr.head_sha
    base_ref = pr.base_ref
    base_sha = pr.base_sha

    if repository.disk_path and os.path.isdir(repository.disk_path):
        head_ref, resolved_head_sha = await normalize_branch_ref(
            repository.disk_path, pr.head_ref, owner_login
        )
        base_ref, resolved_base_sha = await normalize_branch_ref(
            repository.disk_path, pr.base_ref, owner_login
        )
        head_sha = resolved_head_sha or head_sha
        base_sha = resolved_base_sha or base_sha

    pr.resolved_head_ref = head_ref
    pr.resolved_base_ref = base_ref
    pr.resolved_head_sha = head_sha
    pr.resolved_base_sha = base_sha
    pr.resolved_head_label = f"{owner_login or 'unknown'}:{head_ref}"


def _commit_json(owner: str, repo: str, commit: dict) -> dict:
    """Build a GitHub-compatible commit JSON object from git log data."""
    sha = commit["sha"]
    api = f"{BASE}/api/v3"
    return {
        "sha": sha,
        "node_id": _make_node_id("Commit", hash(sha) % 10**8),
        "commit": {
            "author": {
                "name": commit.get("author_name"),
                "email": commit.get("author_email"),
                "date": commit.get("author_date"),
            },
            "committer": {
                "name": commit.get("committer_name"),
                "email": commit.get("committer_email"),
                "date": commit.get("committer_date"),
            },
            "message": "\n\n".join(
                part
                for part in [commit.get("message", ""), commit.get("body", "")]
                if part
            ),
            "tree": {"sha": commit.get("tree_sha"), "url": ""},
            "url": f"{api}/repos/{owner}/{repo}/git/commits/{sha}",
            "comment_count": 0,
        },
        "url": f"{api}/repos/{owner}/{repo}/commits/{sha}",
        "html_url": f"{BASE}/{owner}/{repo}/commit/{sha}",
        "parents": [
            {
                "sha": parent,
                "url": f"{api}/repos/{owner}/{repo}/commits/{parent}",
                "html_url": f"{BASE}/{owner}/{repo}/commit/{parent}",
            }
            for parent in commit.get("parent_shas", [])
        ],
    }


def _placeholder_pr_commit_json(owner: str, repo: str, pr: PullRequest) -> dict:
    """Build the legacy placeholder PR commit response for unresolved refs."""
    sha = pr.resolved_head_sha
    return {
        "sha": sha,
        "node_id": _make_node_id("Commit", hash(sha) % 10**8),
        "commit": {
            "author": {
                "name": "unknown",
                "email": "unknown",
                "date": _fmt_dt(pr.issue.created_at),
            },
            "committer": {
                "name": "unknown",
                "email": "unknown",
                "date": _fmt_dt(pr.issue.created_at),
            },
            "message": pr.issue.title,
            "tree": {"sha": "0" * 40, "url": ""},
            "url": "",
            "comment_count": 0,
        },
        "url": f"{BASE}/api/v3/repos/{owner}/{repo}/commits/{sha}",
        "html_url": f"{BASE}/{owner}/{repo}/commit/{sha}",
        "parents": [],
    }


def _pull_file_json(owner: str, repo: str, file: dict) -> dict:
    """Build a GitHub-compatible pull-request changed-file JSON object."""
    filename = file.get("filename", "")
    return {
        "sha": file.get("sha", ""),
        "filename": filename,
        "status": file.get("status", "modified"),
        "additions": file.get("additions", 0),
        "deletions": file.get("deletions", 0),
        "changes": file.get("changes", 0),
        "blob_url": f"{BASE}/{owner}/{repo}/blob/HEAD/{filename}",
        "raw_url": f"{BASE}/{owner}/{repo}/raw/HEAD/{filename}",
        "contents_url": f"{BASE}/api/v3/repos/{owner}/{repo}/contents/{filename}",
        "patch": file.get("patch", ""),
        **(
            {"previous_filename": file["previous_filename"]}
            if file.get("previous_filename")
            else {}
        ),
    }


async def _run_git(git_dir: str, *args: str, env_extra: dict | None = None) -> tuple[int, str, str]:
    """Run a git command with GIT_DIR set.  Returns (returncode, stdout, stderr)."""
    env = {**os.environ, "GIT_DIR": git_dir}
    if env_extra:
        env.update(env_extra)
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    return proc.returncode, stdout_bytes.decode(), stderr_bytes.decode()


def _merge_env(work_tree: str | None = None) -> dict[str, str]:
    env = {
        "GIT_AUTHOR_NAME": "GitHub Emulator",
        "GIT_AUTHOR_EMAIL": "noreply@github-emulator.local",
        "GIT_COMMITTER_NAME": "GitHub Emulator",
        "GIT_COMMITTER_EMAIL": "noreply@github-emulator.local",
    }
    if work_tree:
        env["GIT_WORK_TREE"] = work_tree
    return env


async def _perform_git_merge(
    disk_path: str,
    head_ref: str,
    base_ref: str,
    merge_method: str,
    commit_message: str,
) -> str | None:
    """Perform a real git merge/squash/rebase in the bare repo.

    Because bare repos cannot use ``git merge`` directly, we clone into a
    temporary working tree, perform the merge there, then push the result
    back into the bare repo.

    Returns the merge-commit SHA on success, or *None* if the git operations
    fail for any reason (missing branches, conflicts, etc.).
    """
    if not disk_path or not os.path.isdir(disk_path):
        logger.warning("Bare repo path %s does not exist; skipping git merge", disk_path)
        return None

    # Verify that both refs exist in the bare repo
    for ref in (head_ref, base_ref):
        rc, _, err = await _run_git(disk_path, "rev-parse", "--verify", ref)
        if rc != 0:
            logger.warning(
                "Ref %s not found in bare repo %s (%s); skipping git merge",
                ref, disk_path, err.strip(),
            )
            return None

    # We need a working tree.  Clone the bare repo into a temp dir, perform
    # the merge, and push the updated base_ref back.
    tmpdir = tempfile.mkdtemp(prefix="gh_emu_merge_")
    clone_path = os.path.join(tmpdir, "work")
    try:
        # Clone from the bare repo (local, no network)
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--no-checkout", disk_path, clone_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await proc.communicate()
        if proc.returncode != 0:
            logger.warning("git clone failed: %s", stderr_bytes.decode().strip())
            return None

        work_git_dir = os.path.join(clone_path, ".git")

        # Checkout the base branch
        rc, _, err = await _run_git(
            work_git_dir, "checkout", base_ref,
            env_extra=_merge_env(clone_path),
        )
        if rc != 0:
            logger.warning("git checkout %s failed: %s", base_ref, err.strip())
            return None

        merge_sha: str | None = None

        if merge_method == "squash":
            # git merge --squash <head_ref> && git commit
            rc, _, err = await _run_git(
                work_git_dir, "merge", "--squash", f"origin/{head_ref}",
                env_extra=_merge_env(clone_path),
            )
            if rc != 0:
                logger.warning("git merge --squash failed: %s", err.strip())
                return None

            rc, _, err = await _run_git(
                work_git_dir, "commit", "-m", commit_message,
                "--author", "GitHub Emulator <noreply@github-emulator.local>",
                env_extra=_merge_env(clone_path),
            )
            if rc != 0:
                logger.warning("git commit (squash) failed: %s", err.strip())
                return None

            rc, out, _ = await _run_git(work_git_dir, "rev-parse", "HEAD")
            if rc == 0:
                merge_sha = out.strip()

        elif merge_method == "rebase":
            # Rebase head_ref onto base_ref, then fast-forward base_ref.
            # We checkout head_ref, rebase onto base_ref, then update base_ref
            # pointer.
            rc, _, err = await _run_git(
                work_git_dir, "checkout", f"origin/{head_ref}",
                env_extra=_merge_env(clone_path),
            )
            if rc != 0:
                logger.warning("git checkout origin/%s failed: %s", head_ref, err.strip())
                return None

            rc, _, err = await _run_git(
                work_git_dir, "rebase", base_ref,
                env_extra=_merge_env(clone_path),
            )
            if rc != 0:
                logger.warning("git rebase failed: %s", err.strip())
                # Abort the rebase so we don't leave the clone in a broken state
                await _run_git(
                    work_git_dir, "rebase", "--abort",
                    env_extra=_merge_env(clone_path),
                )
                return None

            # Move the base_ref branch pointer to the rebased HEAD
            rc, rebased_sha, _ = await _run_git(work_git_dir, "rev-parse", "HEAD")
            if rc != 0:
                return None
            rebased_sha = rebased_sha.strip()

            rc, _, err = await _run_git(
                work_git_dir, "branch", "-f", base_ref, rebased_sha,
                env_extra=_merge_env(clone_path),
            )
            if rc != 0:
                logger.warning("git branch -f %s failed: %s", base_ref, err.strip())
                return None

            rc, _, err = await _run_git(
                work_git_dir, "checkout", base_ref,
                env_extra=_merge_env(clone_path),
            )
            if rc != 0:
                logger.warning("git checkout %s after rebase failed: %s", base_ref, err.strip())
                return None

            merge_sha = rebased_sha

        else:
            # Default: standard merge commit
            rc, _, err = await _run_git(
                work_git_dir, "merge", "--no-ff", f"origin/{head_ref}",
                "-m", commit_message,
                env_extra=_merge_env(clone_path),
            )
            if rc != 0:
                logger.warning("git merge failed: %s", err.strip())
                return None

            rc, out, _ = await _run_git(work_git_dir, "rev-parse", "HEAD")
            if rc == 0:
                merge_sha = out.strip()

        if not merge_sha:
            return None

        # Push the updated base_ref back to the bare repo
        rc, _, err = await _run_git(
            work_git_dir, "push", "origin", base_ref,
            env_extra=_merge_env(clone_path),
        )
        if rc != 0:
            logger.warning("git push to bare repo failed: %s", err.strip())
            return None

        logger.info(
            "Git %s succeeded: %s -> %s = %s",
            merge_method, head_ref, base_ref, merge_sha,
        )
        return merge_sha

    except Exception:
        logger.exception("Unexpected error during git %s", merge_method)
        return None
    finally:
        # Clean up the temp clone
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/repos/{owner}/{repo}/pulls")
async def list_pulls(
    owner: str,
    repo: str,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    state: str = Query("open"),
    head: Optional[str] = Query(None),
    base: Optional[str] = Query(None),
    sort: str = Query("created"),
    direction: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List pull requests."""
    repository = await get_repo_or_404(owner, repo, db)

    query = (
        _pr_query()
        .join(Issue, PullRequest.issue_id == Issue.id)
        .where(PullRequest.repo_id == repository.id)
    )

    if state != "all":
        query = query.where(Issue.state == state)
    if head:
        query = query.where(PullRequest.head_ref == head)
    if base:
        query = query.where(PullRequest.base_ref == base)

    # Sorting
    if sort == "updated":
        sort_col = Issue.updated_at
    elif sort == "popularity":
        sort_col = Issue.created_at
    elif sort == "long-running":
        sort_col = Issue.created_at
    else:
        sort_col = Issue.created_at

    if direction == "asc":
        query = query.order_by(sort_col.asc())
    else:
        query = query.order_by(sort_col.desc())

    count_q = select(sa_func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.offset((page - 1) * per_page).limit(per_page)
    prs = (await db.execute(query)).scalars().all()

    headers = {}
    last_page = max(1, (total + per_page - 1) // per_page)
    parts: list[str] = []
    base_url_str = str(request.url).split("?")[0]
    if page < last_page:
        parts.append(f'<{base_url_str}?page={page + 1}&per_page={per_page}>; rel="next"')
        parts.append(f'<{base_url_str}?page={last_page}&per_page={per_page}>; rel="last"')
    if page > 1:
        parts.append(f'<{base_url_str}?page={page - 1}&per_page={per_page}>; rel="prev"')
        parts.append(f'<{base_url_str}?page=1&per_page={per_page}>; rel="first"')
    if parts:
        headers["Link"] = ", ".join(parts)

    for pr in prs:
        await _attach_resolved_refs(pr, repository)

    return JSONResponse(
        content=[_pr_json(pr, BASE) for pr in prs],
        headers=headers,
    )


@router.post("/repos/{owner}/{repo}/pulls", status_code=201)
async def create_pull(
    owner: str, repo: str, body: dict, user: AuthUser, db: DbSession
):
    """Create a pull request."""
    repository = await get_repo_or_404(owner, repo, db)

    title = body.get("title")
    head_ref = body.get("head")
    base_ref = body.get("base")
    if not title or not head_ref or not base_ref:
        raise HTTPException(
            status_code=422, detail="title, head, and base are required"
        )

    # Create the associated issue
    number = repository.next_issue_number
    repository.next_issue_number = number + 1
    repository.open_issues_count += 1

    issue = Issue(
        repo_id=repository.id,
        number=number,
        user_id=user.id,
        title=title,
        body=body.get("body"),
    )
    db.add(issue)
    await db.flush()

    head_sha = body.get("head_sha", "0" * 40)
    base_sha = body.get("base_sha", "0" * 40)
    owner_login = repository.owner.login if repository.owner else owner

    # Try to resolve SHAs from the bare repo
    if repository.disk_path and os.path.isdir(repository.disk_path):
        normalized_head_ref, resolved_head_sha = await normalize_branch_ref(
            repository.disk_path, head_ref, owner_login
        )
        normalized_base_ref, resolved_base_sha = await normalize_branch_ref(
            repository.disk_path, base_ref, owner_login
        )
        head_ref = normalized_head_ref
        base_ref = normalized_base_ref
        head_sha = resolved_head_sha or head_sha
        base_sha = resolved_base_sha or base_sha

    pr = PullRequest(
        issue_id=issue.id,
        repo_id=repository.id,
        head_ref=head_ref,
        head_sha=head_sha,
        base_ref=base_ref,
        base_sha=base_sha,
        draft=body.get("draft", False),
    )
    db.add(pr)
    await db.commit()

    # Re-query with eager loading
    result = await db.execute(
        _pr_query().where(PullRequest.id == pr.id)
    )
    pr = result.scalar_one()
    await _attach_resolved_refs(pr, repository)
    from app.services.workflow_service import build_activity_payload, dispatch_event
    await dispatch_event(
        db,
        repository,
        user,
        "pull_request_target",
        "opened",
        build_activity_payload(
            repository,
            user,
            "opened",
            issue=pr.issue,
            pull_request=pr,
            ref=f"refs/heads/{pr.base_ref}",
            sha=pr.base_sha,
        ),
        ref=pr.base_ref,
        sha=pr.base_sha,
    )
    return _pr_json(pr, BASE)


@router.get("/repos/{owner}/{repo}/pulls/{pull_number}")
async def get_pull(
    owner: str, repo: str, pull_number: int, db: DbSession, current_user: CurrentUser
):
    """Get a single pull request."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        _pr_query()
        .join(Issue, PullRequest.issue_id == Issue.id)
        .where(PullRequest.repo_id == repository.id, Issue.number == pull_number)
    )
    pr = result.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail="Not Found")

    await _attach_resolved_refs(pr, repository)
    return _pr_json(pr, BASE)


@router.patch("/repos/{owner}/{repo}/pulls/{pull_number}")
async def update_pull(
    owner: str,
    repo: str,
    pull_number: int,
    body: dict,
    user: AuthUser,
    db: DbSession,
):
    """Update a pull request."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        _pr_query()
        .join(Issue, PullRequest.issue_id == Issue.id)
        .where(PullRequest.repo_id == repository.id, Issue.number == pull_number)
    )
    pr = result.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail="Not Found")

    issue = pr.issue
    old_state = issue.state
    old_title = issue.title
    old_body = issue.body
    old_base = pr.base_ref
    old_draft = pr.draft
    if "title" in body:
        issue.title = body["title"]
    if "body" in body:
        issue.body = body["body"]
    if "state" in body:
        issue.state = body["state"]
        if body["state"] == "closed" and old_state != "closed":
            issue.closed_at = datetime.now(timezone.utc)
            repository.open_issues_count = max(0, repository.open_issues_count - 1)
        elif body["state"] == "open" and old_state != "open":
            issue.closed_at = None
            repository.open_issues_count += 1
    if "base" in body:
        pr.base_ref = body["base"]
    if "draft" in body:
        pr.draft = body["draft"]

    await db.commit()

    # Re-query with eager loading
    result = await db.execute(
        _pr_query().where(PullRequest.id == pr.id)
    )
    pr = result.scalar_one()
    await _attach_resolved_refs(pr, repository)
    from app.services.workflow_service import build_activity_payload, dispatch_event
    action = "edited"
    if issue.state != old_state:
        action = "closed" if issue.state == "closed" else "reopened"
    elif old_draft and not pr.draft:
        action = "ready_for_review"
    elif not old_draft and pr.draft:
        action = "converted_to_draft"
    changed = (
        issue.state != old_state
        or issue.title != old_title
        or issue.body != old_body
        or pr.base_ref != old_base
        or pr.draft != old_draft
    )
    if changed:
        await dispatch_event(
            db,
            repository,
            user,
            "pull_request_target",
            action,
            build_activity_payload(
                repository,
                user,
                action,
                issue=issue,
                pull_request=pr,
                ref=f"refs/heads/{pr.base_ref}",
                sha=pr.base_sha,
            ),
            ref=pr.base_ref,
            sha=pr.base_sha,
        )
    return _pr_json(pr, BASE)


@router.put("/repos/{owner}/{repo}/pulls/{pull_number}/merge")
async def merge_pull(
    owner: str,
    repo: str,
    pull_number: int,
    body: dict,
    user: AuthUser,
    db: DbSession,
):
    """Merge a pull request."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        _pr_query()
        .join(Issue, PullRequest.issue_id == Issue.id)
        .where(PullRequest.repo_id == repository.id, Issue.number == pull_number)
    )
    pr = result.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await _attach_resolved_refs(pr, repository)

    if pr.merged:
        raise HTTPException(status_code=405, detail="Pull request already merged")

    issue = pr.issue
    if issue.state == "closed":
        raise HTTPException(status_code=422, detail="Pull request is closed")

    now = datetime.now(timezone.utc)
    merge_method = body.get("merge_method", "merge")
    commit_title = body.get("commit_title", f"Merge pull request #{pull_number}")
    commit_message = body.get(
        "commit_message",
        f"{commit_title}\n\nMerge {pr.head_ref} into {pr.base_ref}",
    )

    pr.merged = True
    pr.merged_at = now
    pr.merged_by_id = user.id
    pr.merge_commit_sha = body.get("sha", pr.resolved_head_sha)
    issue.state = "closed"
    issue.closed_at = now
    repository.open_issues_count = max(0, repository.open_issues_count - 1)

    # --- Perform actual git operations in the bare repo ---
    try:
        git_sha = await _perform_git_merge(
            disk_path=repository.disk_path,
            head_ref=pr.resolved_head_ref,
            base_ref=pr.resolved_base_ref,
            merge_method=merge_method,
            commit_message=commit_message,
        )
        if git_sha:
            pr.merge_commit_sha = git_sha
            logger.info(
                "PR #%d merged via git (%s): sha=%s",
                pull_number, merge_method, git_sha,
            )
        else:
            logger.warning(
                "PR #%d: git %s did not produce a SHA; using DB-only merge",
                pull_number, merge_method,
            )
    except Exception:
        logger.exception(
            "PR #%d: git merge failed; falling back to DB-only merge",
            pull_number,
        )

    await db.commit()

    # The merge mutates both rows and SQLite may expire server-managed
    # timestamps; refresh before constructing the event payload so dispatch
    # never performs an implicit async lazy load.
    await db.refresh(issue)
    await db.refresh(pr)

    from app.services.workflow_service import build_activity_payload, dispatch_event
    await dispatch_event(
        db,
        repository,
        user,
        "pull_request_target",
        "closed",
        build_activity_payload(
            repository,
            user,
            "closed",
            issue=issue,
            pull_request=pr,
            ref=f"refs/heads/{pr.base_ref}",
            sha=pr.merge_commit_sha or pr.base_sha,
        ),
        ref=pr.base_ref,
        sha=pr.merge_commit_sha or pr.base_sha,
    )

    return {
        "sha": pr.merge_commit_sha,
        "merged": True,
        "message": "Pull Request successfully merged",
    }


@router.get("/repos/{owner}/{repo}/pulls/{pull_number}/commits")
async def list_pull_commits(
    owner: str,
    repo: str,
    pull_number: int,
    db: DbSession,
    current_user: CurrentUser,
):
    """List commits on a pull request."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        _pr_query()
        .join(Issue, PullRequest.issue_id == Issue.id)
        .where(PullRequest.repo_id == repository.id, Issue.number == pull_number)
    )
    pr = result.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await _attach_resolved_refs(pr, repository)

    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        return [_placeholder_pr_commit_json(owner, repo, pr)]

    commits = await get_log(
        repository.disk_path, ref=f"{pr.resolved_base_ref}..{pr.resolved_head_ref}"
    )
    if not commits:
        return [_placeholder_pr_commit_json(owner, repo, pr)]
    return [_commit_json(owner, repo, commit) for commit in commits]


@router.get("/repos/{owner}/{repo}/pulls/{pull_number}/files")
async def list_pull_files(
    owner: str,
    repo: str,
    pull_number: int,
    db: DbSession,
    current_user: CurrentUser,
):
    """List files changed by a pull request."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        _pr_query()
        .join(Issue, PullRequest.issue_id == Issue.id)
        .where(PullRequest.repo_id == repository.id, Issue.number == pull_number)
    )
    pr = result.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await _attach_resolved_refs(pr, repository)

    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        return []

    files = await get_compare_diff(
        repository.disk_path, pr.resolved_base_ref, pr.resolved_head_ref
    )
    return [_pull_file_json(owner, repo, file) for file in files]
