"""Issue comment endpoints -- list, create, get, update, delete."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, func as sa_func

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.comment import IssueComment
from app.models.issue import Issue
from app.schemas.user import SimpleUser, _fmt_dt, _make_node_id

router = APIRouter(tags=["comments"])

BASE = settings.BASE_URL


def _comment_json(comment: IssueComment, owner: str, repo_name: str, issue_number: int, base_url: str) -> dict:
    api = f"{base_url}/api/v3"
    repo_full = f"{owner}/{repo_name}"
    user_simple = SimpleUser.from_db(comment.user, base_url).model_dump() if comment.user else None

    return {
        "id": comment.id,
        "node_id": _make_node_id("IssueComment", comment.id),
        "url": f"{api}/repos/{repo_full}/issues/comments/{comment.id}",
        "html_url": f"{base_url}/{repo_full}/issues/{issue_number}#issuecomment-{comment.id}",
        "body": comment.body,
        "user": user_simple,
        "created_at": _fmt_dt(comment.created_at),
        "updated_at": _fmt_dt(comment.updated_at),
        "issue_url": f"{api}/repos/{repo_full}/issues/{issue_number}",
        "author_association": "OWNER",
        "performed_via_github_app": None,
        "reactions": {
            "url": f"{api}/repos/{repo_full}/issues/comments/{comment.id}/reactions",
            "total_count": 0,
            "+1": 0, "-1": 0, "laugh": 0, "hooray": 0,
            "confused": 0, "heart": 0, "rocket": 0, "eyes": 0,
        },
    }


@router.get("/repos/{owner}/{repo}/issues/{issue_number}/comments")
async def list_comments(
    owner: str,
    repo: str,
    issue_number: int,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
    since: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List comments on an issue."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        select(Issue).where(
            Issue.repo_id == repository.id, Issue.number == issue_number
        )
    )
    issue = result.scalar_one_or_none()
    if issue is None:
        raise HTTPException(status_code=404, detail="Not Found")

    query = select(IssueComment).where(IssueComment.issue_id == issue.id)
    query = query.order_by(IssueComment.created_at.asc())

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
            _comment_json(c, owner, repo, issue_number, BASE) for c in comments
        ],
        headers=headers,
    )


@router.post("/repos/{owner}/{repo}/issues/{issue_number}/comments", status_code=201)
async def create_comment(
    owner: str,
    repo: str,
    issue_number: int,
    body: dict,
    user: AuthUser,
    db: DbSession,
):
    """Create a comment on an issue."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        select(Issue).where(
            Issue.repo_id == repository.id, Issue.number == issue_number
        )
    )
    issue = result.scalar_one_or_none()
    if issue is None:
        raise HTTPException(status_code=404, detail="Not Found")

    comment_body = body.get("body")
    if not comment_body:
        raise HTTPException(status_code=422, detail="body is required")

    comment = IssueComment(
        issue_id=issue.id,
        user_id=user.id,
        body=comment_body,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)

    return _comment_json(comment, owner, repo, issue_number, BASE)


@router.get("/repos/{owner}/{repo}/issues/comments/{comment_id}")
async def get_comment(
    owner: str, repo: str, comment_id: int, db: DbSession, current_user: CurrentUser
):
    """Get a single issue comment."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        select(IssueComment).where(IssueComment.id == comment_id)
    )
    comment = result.scalar_one_or_none()
    if comment is None:
        raise HTTPException(status_code=404, detail="Not Found")

    # Resolve the issue number
    issue_result = await db.execute(
        select(Issue).where(Issue.id == comment.issue_id)
    )
    issue = issue_result.scalar_one_or_none()
    issue_number = issue.number if issue else 0

    return _comment_json(comment, owner, repo, issue_number, BASE)


@router.patch("/repos/{owner}/{repo}/issues/comments/{comment_id}")
async def update_comment(
    owner: str,
    repo: str,
    comment_id: int,
    body: dict,
    user: AuthUser,
    db: DbSession,
):
    """Update an issue comment."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        select(IssueComment).where(IssueComment.id == comment_id)
    )
    comment = result.scalar_one_or_none()
    if comment is None:
        raise HTTPException(status_code=404, detail="Not Found")

    if "body" in body:
        comment.body = body["body"]

    await db.commit()
    await db.refresh(comment)

    issue_result = await db.execute(select(Issue).where(Issue.id == comment.issue_id))
    issue = issue_result.scalar_one_or_none()
    issue_number = issue.number if issue else 0

    return _comment_json(comment, owner, repo, issue_number, BASE)


@router.delete("/repos/{owner}/{repo}/issues/comments/{comment_id}", status_code=204)
async def delete_comment(
    owner: str, repo: str, comment_id: int, user: AuthUser, db: DbSession
):
    """Delete an issue comment."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        select(IssueComment).where(IssueComment.id == comment_id)
    )
    comment = result.scalar_one_or_none()
    if comment is None:
        raise HTTPException(status_code=404, detail="Not Found")

    await db.delete(comment)
    await db.commit()
