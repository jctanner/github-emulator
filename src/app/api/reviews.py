"""PR review endpoints -- list, create, get, submit, dismiss reviews."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.comment import PRReviewComment
from app.models.review import Review
from app.models.pull_request import PullRequest
from app.models.issue import Issue
from app.schemas.user import SimpleUser, _fmt_dt, _make_node_id

router = APIRouter(tags=["reviews"])

BASE = settings.BASE_URL


def _review_json(review: Review, owner: str, repo_name: str, pr_number: int, base_url: str) -> dict:
    api = f"{base_url}/api/v3"
    user_simple = SimpleUser.from_db(review.user, base_url).model_dump() if review.user else None
    return {
        "id": review.id,
        "node_id": _make_node_id("PullRequestReview", review.id),
        "user": user_simple,
        "body": review.body,
        "state": review.state,
        "html_url": f"{base_url}/{owner}/{repo_name}/pull/{pr_number}#pullrequestreview-{review.id}",
        "pull_request_url": f"{api}/repos/{owner}/{repo_name}/pulls/{pr_number}",
        "submitted_at": _fmt_dt(review.submitted_at),
        "commit_id": review.commit_id,
        "author_association": "OWNER",
        "_links": {
            "html": {"href": f"{base_url}/{owner}/{repo_name}/pull/{pr_number}#pullrequestreview-{review.id}"},
            "pull_request": {"href": f"{api}/repos/{owner}/{repo_name}/pulls/{pr_number}"},
        },
    }


async def _get_pr(owner: str, repo: str, pull_number: int, db):
    """Resolve owner/repo/pull_number to a PullRequest."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(PullRequest)
        .join(Issue, PullRequest.issue_id == Issue.id)
        .where(PullRequest.repo_id == repository.id, Issue.number == pull_number)
    )
    pr = result.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return pr


@router.get("/repos/{owner}/{repo}/pulls/{pull_number}/reviews")
async def list_reviews(
    owner: str, repo: str, pull_number: int, db: DbSession, current_user: CurrentUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List reviews for a pull request."""
    pr = await _get_pr(owner, repo, pull_number, db)

    query = (
        select(Review)
        .where(Review.pull_request_id == pr.id)
        .order_by(Review.created_at.asc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    reviews = (await db.execute(query)).scalars().all()
    return [_review_json(r, owner, repo, pull_number, BASE) for r in reviews]


@router.post("/repos/{owner}/{repo}/pulls/{pull_number}/reviews", status_code=201)
async def create_review(
    owner: str, repo: str, pull_number: int, body: dict, user: AuthUser, db: DbSession,
):
    """Create a review."""
    pr = await _get_pr(owner, repo, pull_number, db)

    event = body.get("event", "PENDING")
    review_body = body.get("body")
    commit_id = body.get("commit_id", pr.head_sha)

    # A review may carry its inline comments in one request, which is how a
    # client posts a multi-file review atomically rather than as N+1 calls.
    # These were accepted and silently discarded: the review appeared with the
    # right state and body, the caller reported success, and every inline
    # finding vanished. Validated before the review is written so a malformed
    # entry rejects the whole request, as it does on GitHub, rather than
    # leaving a review behind with some of its comments.
    raw_comments = body.get("comments") or []
    if not isinstance(raw_comments, list):
        raise HTTPException(status_code=422, detail="comments must be an array")
    pending_comments = []
    for index, entry in enumerate(raw_comments):
        if not isinstance(entry, dict):
            raise HTTPException(
                status_code=422, detail=f"comments[{index}] must be an object"
            )
        entry_path = entry.get("path")
        entry_body = entry.get("body")
        if not entry_path:
            raise HTTPException(
                status_code=422, detail=f"comments[{index}].path is required"
            )
        if not entry_body:
            raise HTTPException(
                status_code=422, detail=f"comments[{index}].body is required"
            )
        # GitHub accepts either the legacy diff `position` or the newer `line`,
        # and a review is free to mix them across entries. A comment on a whole
        # file carries neither and says so with subject_type="file" - which is
        # what Fullsend sends for a file-level finding, omitting `line`
        # entirely. Requiring an anchor unconditionally would reject exactly
        # those, and the client's 422 fallback drops every inline comment in
        # the review rather than the one it could not place.
        subject_type = (entry.get("subject_type") or "line").lower()
        if subject_type not in ("line", "file"):
            raise HTTPException(
                status_code=422,
                detail=f"comments[{index}].subject_type must be line or file",
            )
        if (
            subject_type == "line"
            and entry.get("position") is None
            and entry.get("line") is None
        ):
            raise HTTPException(
                status_code=422,
                detail=f"comments[{index}] requires either position or line",
            )
        entry["subject_type"] = subject_type
        pending_comments.append(entry)

    now = datetime.now(timezone.utc)
    state = {
        "APPROVE": "APPROVED",
        "REQUEST_CHANGES": "CHANGES_REQUESTED",
        "COMMENT": "COMMENTED",
        "PENDING": "PENDING",
    }.get(event.upper() if event else "PENDING")
    if state is None:
        raise HTTPException(status_code=422, detail="Invalid event")

    review = Review(
        pull_request_id=pr.id,
        user_id=user.id,
        body=review_body,
        state=state,
        commit_id=commit_id,
        submitted_at=now if state != "PENDING" else None,
    )
    db.add(review)
    await db.commit()
    await db.refresh(review)

    # Attached to the review by id, so they come back from both the review's
    # own comments endpoint and the pull request's.
    for entry in pending_comments:
        entry_commit = entry.get("commit_id") or commit_id or ""
        db.add(
            PRReviewComment(
                pull_request_id=pr.id,
                review_id=review.id,
                user_id=user.id,
                body=entry["body"],
                path=entry["path"],
                position=entry.get("position"),
                line=entry.get("line"),
                side=entry.get("side", "RIGHT"),
                commit_id=entry_commit,
                original_commit_id=entry_commit,
                diff_hunk=entry.get("diff_hunk"),
                in_reply_to_id=entry.get("in_reply_to_id"),
                subject_type=entry.get("subject_type", "line"),
            )
        )
    if pending_comments:
        await db.commit()

    if state != "PENDING":
        from app.services.workflow_service import build_activity_payload, dispatch_event
        await dispatch_event(
            db,
            pr.repository,
            user,
            "pull_request_review",
            "submitted",
            build_activity_payload(
                pr.repository,
                user,
                "submitted",
                issue=pr.issue,
                pull_request=pr,
                review=review,
                ref=f"refs/heads/{pr.base_ref}",
                sha=pr.base_sha,
            ),
            ref=pr.base_ref,
            sha=pr.base_sha,
        )
        from app.services.auto_merge_service import process_auto_merge
        await process_auto_merge(db, pr, user)
    return _review_json(review, owner, repo, pull_number, BASE)


@router.get("/repos/{owner}/{repo}/pulls/{pull_number}/reviews/{review_id}")
async def get_review(
    owner: str, repo: str, pull_number: int, review_id: int,
    db: DbSession, current_user: CurrentUser,
):
    """Get a single review."""
    pr = await _get_pr(owner, repo, pull_number, db)
    result = await db.execute(
        select(Review).where(Review.id == review_id, Review.pull_request_id == pr.id)
    )
    review = result.scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _review_json(review, owner, repo, pull_number, BASE)


@router.put("/repos/{owner}/{repo}/pulls/{pull_number}/reviews/{review_id}/events")
async def submit_review(
    owner: str, repo: str, pull_number: int, review_id: int,
    body: dict, user: AuthUser, db: DbSession,
):
    """Submit a pending review."""
    pr = await _get_pr(owner, repo, pull_number, db)
    result = await db.execute(
        select(Review).where(Review.id == review_id, Review.pull_request_id == pr.id)
    )
    review = result.scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail="Not Found")

    event = body.get("event", "").upper()
    if event not in ("APPROVE", "REQUEST_CHANGES", "COMMENT"):
        raise HTTPException(status_code=422, detail="Invalid event")

    state_map = {"APPROVE": "APPROVED", "REQUEST_CHANGES": "CHANGES_REQUESTED", "COMMENT": "COMMENTED"}
    review.state = state_map[event]
    review.submitted_at = datetime.now(timezone.utc)
    if "body" in body:
        review.body = body["body"]

    await db.commit()
    await db.refresh(review)
    from app.services.workflow_service import build_activity_payload, dispatch_event
    await dispatch_event(
        db,
        pr.repository,
        user,
        "pull_request_review",
        "submitted",
        build_activity_payload(
            pr.repository,
            user,
            "submitted",
            issue=pr.issue,
            pull_request=pr,
            review=review,
            ref=f"refs/heads/{pr.base_ref}",
            sha=pr.base_sha,
        ),
        ref=pr.base_ref,
        sha=pr.base_sha,
    )
    from app.services.auto_merge_service import process_auto_merge
    await process_auto_merge(db, pr, user)
    return _review_json(review, owner, repo, pull_number, BASE)


@router.put("/repos/{owner}/{repo}/pulls/{pull_number}/reviews/{review_id}/dismissals")
async def dismiss_review(
    owner: str, repo: str, pull_number: int, review_id: int,
    body: dict, user: AuthUser, db: DbSession,
):
    """Dismiss a review."""
    pr = await _get_pr(owner, repo, pull_number, db)
    result = await db.execute(
        select(Review).where(Review.id == review_id, Review.pull_request_id == pr.id)
    )
    review = result.scalar_one_or_none()
    if review is None:
        raise HTTPException(status_code=404, detail="Not Found")

    review.state = "DISMISSED"
    await db.commit()
    await db.refresh(review)
    from app.services.auto_merge_service import process_auto_merge

    await process_auto_merge(db, pr, user)
    return _review_json(review, owner, repo, pull_number, BASE)
