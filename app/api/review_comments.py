"""PR review comment endpoints -- list, create, get, update, delete."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, func as sa_func

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.comment import PRReviewComment
from app.models.pull_request import PullRequest
from app.models.issue import Issue
from app.models.review import Review
from app.schemas.user import SimpleUser, _fmt_dt, _make_node_id

router = APIRouter(tags=["review-comments"])

BASE = settings.BASE_URL


def _review_comment_json(
    comment: PRReviewComment, owner: str, repo_name: str,
    pr_number: int, base_url: str,
) -> dict:
    api = f"{base_url}/api/v3"
    repo_full = f"{owner}/{repo_name}"
    user_simple = SimpleUser.from_db(comment.user, base_url).model_dump() if comment.user else None

    return {
        "id": comment.id,
        "node_id": _make_node_id("PullRequestReviewComment", comment.id),
        "url": f"{api}/repos/{repo_full}/pulls/comments/{comment.id}",
        "pull_request_review_id": comment.review_id,
        "diff_hunk": comment.diff_hunk or "",
        "path": comment.path,
        "position": comment.position,
        "original_position": comment.position,
        "commit_id": comment.commit_id,
        "original_commit_id": comment.original_commit_id or comment.commit_id,
        "in_reply_to_id": comment.in_reply_to_id,
        "user": user_simple,
        "body": comment.body,
        "created_at": _fmt_dt(comment.created_at),
        "updated_at": _fmt_dt(comment.updated_at),
        "html_url": f"{base_url}/{repo_full}/pull/{pr_number}#discussion_r{comment.id}",
        "pull_request_url": f"{api}/repos/{repo_full}/pulls/{pr_number}",
        "author_association": "OWNER",
        "line": comment.line,
        "side": comment.side or "RIGHT",
        "_links": {
            "self": {"href": f"{api}/repos/{repo_full}/pulls/comments/{comment.id}"},
            "html": {"href": f"{base_url}/{repo_full}/pull/{pr_number}#discussion_r{comment.id}"},
            "pull_request": {"href": f"{api}/repos/{repo_full}/pulls/{pr_number}"},
        },
        "reactions": {
            "url": f"{api}/repos/{repo_full}/pulls/comments/{comment.id}/reactions",
            "total_count": 0,
            "+1": 0, "-1": 0, "laugh": 0, "hooray": 0,
            "confused": 0, "heart": 0, "rocket": 0, "eyes": 0,
        },
    }


async def _get_pr(owner: str, repo: str, pull_number: int, db):
    """Resolve owner/repo/pull_number to (repository, pr, issue_number)."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(PullRequest)
        .join(Issue, PullRequest.issue_id == Issue.id)
        .where(PullRequest.repo_id == repository.id, Issue.number == pull_number)
    )
    pr = result.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return repository, pr


@router.get("/repos/{owner}/{repo}/pulls/{pull_number}/comments")
async def list_review_comments(
    owner: str,
    repo: str,
    pull_number: int,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    since: str | None = Query(None),
    sort: str = Query("created"),
    direction: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List review comments on a pull request."""
    repository, pr = await _get_pr(owner, repo, pull_number, db)

    query = select(PRReviewComment).where(PRReviewComment.pull_request_id == pr.id)

    sort_col = PRReviewComment.created_at if sort == "created" else PRReviewComment.updated_at
    if direction == "asc":
        query = query.order_by(sort_col.asc())
    else:
        query = query.order_by(sort_col.desc())

    count_q = select(sa_func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.offset((page - 1) * per_page).limit(per_page)
    comments = (await db.execute(query)).scalars().all()

    headers = {}
    last_page = max(1, (total + per_page - 1) // per_page)
    parts: list[str] = []
    base_url_str = str(request.url).split("?")[0]
    if page < last_page:
        parts.append(f'<{base_url_str}?page={page + 1}&per_page={per_page}>; rel="next"')
    if page > 1:
        parts.append(f'<{base_url_str}?page={page - 1}&per_page={per_page}>; rel="prev"')
    if parts:
        headers["Link"] = ", ".join(parts)

    return JSONResponse(
        content=[
            _review_comment_json(c, owner, repo, pull_number, BASE) for c in comments
        ],
        headers=headers,
    )


@router.post("/repos/{owner}/{repo}/pulls/{pull_number}/comments", status_code=201)
async def create_review_comment(
    owner: str,
    repo: str,
    pull_number: int,
    body: dict,
    user: AuthUser,
    db: DbSession,
):
    """Create a review comment on a pull request."""
    repository, pr = await _get_pr(owner, repo, pull_number, db)

    comment_body = body.get("body")
    if not comment_body:
        raise HTTPException(status_code=422, detail="body is required")

    path = body.get("path", "")
    if not path:
        raise HTTPException(status_code=422, detail="path is required")

    commit_id = body.get("commit_id", pr.head_sha or "")
    position = body.get("position")
    line = body.get("line")
    side = body.get("side", "RIGHT")
    in_reply_to_id = body.get("in_reply_to_id")

    comment = PRReviewComment(
        pull_request_id=pr.id,
        review_id=None,
        user_id=user.id,
        body=comment_body,
        path=path,
        position=position,
        line=line,
        side=side,
        commit_id=commit_id,
        original_commit_id=commit_id,
        diff_hunk=body.get("diff_hunk"),
        in_reply_to_id=in_reply_to_id,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)

    return _review_comment_json(comment, owner, repo, pull_number, BASE)


@router.get("/repos/{owner}/{repo}/pulls/comments/{comment_id}")
async def get_review_comment(
    owner: str, repo: str, comment_id: int, db: DbSession, current_user: CurrentUser,
):
    """Get a single review comment."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        select(PRReviewComment).where(PRReviewComment.id == comment_id)
    )
    comment = result.scalar_one_or_none()
    if comment is None:
        raise HTTPException(status_code=404, detail="Not Found")

    # Resolve PR number
    pr_result = await db.execute(
        select(PullRequest).where(PullRequest.id == comment.pull_request_id)
    )
    pr = pr_result.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail="Not Found")

    issue_result = await db.execute(
        select(Issue).where(Issue.id == pr.issue_id)
    )
    issue = issue_result.scalar_one_or_none()
    pr_number = issue.number if issue else 0

    return _review_comment_json(comment, owner, repo, pr_number, BASE)


@router.patch("/repos/{owner}/{repo}/pulls/comments/{comment_id}")
async def update_review_comment(
    owner: str, repo: str, comment_id: int, body: dict, user: AuthUser, db: DbSession,
):
    """Update a review comment."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        select(PRReviewComment).where(PRReviewComment.id == comment_id)
    )
    comment = result.scalar_one_or_none()
    if comment is None:
        raise HTTPException(status_code=404, detail="Not Found")

    if "body" in body:
        comment.body = body["body"]

    await db.commit()
    await db.refresh(comment)

    pr_result = await db.execute(
        select(PullRequest).where(PullRequest.id == comment.pull_request_id)
    )
    pr = pr_result.scalar_one_or_none()
    issue_result = await db.execute(
        select(Issue).where(Issue.id == pr.issue_id)
    ) if pr else None
    issue = issue_result.scalar_one_or_none() if issue_result else None
    pr_number = issue.number if issue else 0

    return _review_comment_json(comment, owner, repo, pr_number, BASE)


@router.delete("/repos/{owner}/{repo}/pulls/comments/{comment_id}", status_code=204)
async def delete_review_comment(
    owner: str, repo: str, comment_id: int, user: AuthUser, db: DbSession,
):
    """Delete a review comment."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        select(PRReviewComment).where(PRReviewComment.id == comment_id)
    )
    comment = result.scalar_one_or_none()
    if comment is None:
        raise HTTPException(status_code=404, detail="Not Found")

    await db.delete(comment)
    await db.commit()


@router.get("/repos/{owner}/{repo}/pulls/{pull_number}/reviews/{review_id}/comments")
async def list_review_comments_for_review(
    owner: str,
    repo: str,
    pull_number: int,
    review_id: int,
    db: DbSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List comments for a specific review."""
    repository, pr = await _get_pr(owner, repo, pull_number, db)

    # Verify the review exists
    rev_result = await db.execute(
        select(Review).where(Review.id == review_id, Review.pull_request_id == pr.id)
    )
    review = rev_result.scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail="Not Found")

    query = (
        select(PRReviewComment)
        .where(
            PRReviewComment.pull_request_id == pr.id,
            PRReviewComment.review_id == review_id,
        )
        .order_by(PRReviewComment.created_at.asc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    comments = (await db.execute(query)).scalars().all()

    return [_review_comment_json(c, owner, repo, pull_number, BASE) for c in comments]
