"""Milestone endpoints -- CRUD under /repos/{owner}/{repo}/milestones."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, func as sa_func

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.milestone import Milestone
from app.models.issue import Issue
from app.schemas.milestone import MilestoneCreate, MilestoneResponse, MilestoneUpdate

router = APIRouter(tags=["milestones"])

BASE = settings.BASE_URL


async def _count_issues(db, milestone_id: int, state: str) -> int:
    result = await db.execute(
        select(sa_func.count()).where(
            Issue.milestone_id == milestone_id, Issue.state == state
        )
    )
    return result.scalar() or 0


@router.get("/repos/{owner}/{repo}/milestones")
async def list_milestones(
    owner: str,
    repo: str,
    db: DbSession,
    current_user: CurrentUser,
    state: str = Query("open"),
    sort: str = Query("due_on"),
    direction: str = Query("asc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List milestones for a repository."""
    repository = await get_repo_or_404(owner, repo, db)
    query = select(Milestone).where(Milestone.repo_id == repository.id)

    if state != "all":
        query = query.where(Milestone.state == state)

    sort_col = getattr(Milestone, sort, Milestone.due_on)
    if direction == "desc":
        query = query.order_by(sort_col.desc())
    else:
        query = query.order_by(sort_col.asc())

    query = query.offset((page - 1) * per_page).limit(per_page)
    milestones = (await db.execute(query)).scalars().all()

    results = []
    for ms in milestones:
        open_count = await _count_issues(db, ms.id, "open")
        closed_count = await _count_issues(db, ms.id, "closed")
        results.append(
            MilestoneResponse.from_db(
                ms, BASE, owner, repo,
                open_issues=open_count,
                closed_issues=closed_count,
            )
        )
    return results


@router.post("/repos/{owner}/{repo}/milestones", status_code=201)
async def create_milestone(
    owner: str, repo: str, body: MilestoneCreate, user: AuthUser, db: DbSession
):
    """Create a milestone."""
    repository = await get_repo_or_404(owner, repo, db)

    # Get next milestone number
    result = await db.execute(
        select(sa_func.coalesce(sa_func.max(Milestone.number), 0)).where(
            Milestone.repo_id == repository.id
        )
    )
    next_number = (result.scalar() or 0) + 1

    due_on = None
    if body.due_on:
        try:
            due_on = datetime.fromisoformat(body.due_on.replace("Z", "+00:00"))
        except ValueError:
            pass

    milestone = Milestone(
        repo_id=repository.id,
        number=next_number,
        title=body.title,
        description=body.description,
        state=body.state,
        due_on=due_on,
    )
    db.add(milestone)
    await db.commit()
    await db.refresh(milestone)
    return MilestoneResponse.from_db(milestone, BASE, owner, repo)


@router.get("/repos/{owner}/{repo}/milestones/{milestone_number}")
async def get_milestone(
    owner: str, repo: str, milestone_number: int, db: DbSession, current_user: CurrentUser
):
    """Get a single milestone."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Milestone).where(
            Milestone.repo_id == repository.id,
            Milestone.number == milestone_number,
        )
    )
    milestone = result.scalar_one_or_none()
    if milestone is None:
        raise HTTPException(status_code=404, detail="Not Found")

    open_count = await _count_issues(db, milestone.id, "open")
    closed_count = await _count_issues(db, milestone.id, "closed")
    return MilestoneResponse.from_db(
        milestone, BASE, owner, repo,
        open_issues=open_count, closed_issues=closed_count,
    )


@router.patch("/repos/{owner}/{repo}/milestones/{milestone_number}")
async def update_milestone(
    owner: str,
    repo: str,
    milestone_number: int,
    body: MilestoneUpdate,
    user: AuthUser,
    db: DbSession,
):
    """Update a milestone."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Milestone).where(
            Milestone.repo_id == repository.id,
            Milestone.number == milestone_number,
        )
    )
    milestone = result.scalar_one_or_none()
    if milestone is None:
        raise HTTPException(status_code=404, detail="Not Found")

    if body.title is not None:
        milestone.title = body.title
    if body.description is not None:
        milestone.description = body.description
    if body.state is not None:
        old_state = milestone.state
        milestone.state = body.state
        if body.state == "closed" and old_state != "closed":
            milestone.closed_at = datetime.now(timezone.utc)
        elif body.state == "open":
            milestone.closed_at = None
    if body.due_on is not None:
        try:
            milestone.due_on = datetime.fromisoformat(body.due_on.replace("Z", "+00:00"))
        except ValueError:
            pass

    await db.commit()
    await db.refresh(milestone)

    open_count = await _count_issues(db, milestone.id, "open")
    closed_count = await _count_issues(db, milestone.id, "closed")
    return MilestoneResponse.from_db(
        milestone, BASE, owner, repo,
        open_issues=open_count, closed_issues=closed_count,
    )


@router.delete("/repos/{owner}/{repo}/milestones/{milestone_number}", status_code=204)
async def delete_milestone(
    owner: str, repo: str, milestone_number: int, user: AuthUser, db: DbSession
):
    """Delete a milestone."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Milestone).where(
            Milestone.repo_id == repository.id,
            Milestone.number == milestone_number,
        )
    )
    milestone = result.scalar_one_or_none()
    if milestone is None:
        raise HTTPException(status_code=404, detail="Not Found")

    await db.delete(milestone)
    await db.commit()
