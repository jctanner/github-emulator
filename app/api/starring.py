"""Starring endpoints -- star/unstar repos, list stargazers and starred."""

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.repository import Repository, StarredRepo
from app.models.user import User
from app.schemas.user import SimpleUser, _fmt_dt
from app.api.repos import _repo_json

router = APIRouter(tags=["starring"])

BASE = settings.BASE_URL


@router.get("/repos/{owner}/{repo}/stargazers")
async def list_stargazers(
    owner: str, repo: str, db: DbSession, current_user: CurrentUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List stargazers of a repository."""
    repository = await get_repo_or_404(owner, repo, db)

    query = (
        select(StarredRepo)
        .where(StarredRepo.repo_id == repository.id)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    stars = (await db.execute(query)).scalars().all()
    return [SimpleUser.from_db(s.user, BASE).model_dump() for s in stars if s.user]


@router.get("/user/starred")
async def list_starred(
    user: AuthUser, db: DbSession,
    sort: str = Query("created"),
    direction: str = Query("desc"),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List repositories starred by the authenticated user."""
    query = (
        select(StarredRepo)
        .where(StarredRepo.user_id == user.id)
        .order_by(StarredRepo.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    stars = (await db.execute(query)).scalars().all()
    return [_repo_json(s.repository, BASE) for s in stars if s.repository]


@router.get("/user/starred/{owner}/{repo}")
async def check_starred(owner: str, repo: str, user: AuthUser, db: DbSession):
    """Check if a repo is starred (204=yes, 404=no)."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        select(StarredRepo).where(
            StarredRepo.user_id == user.id,
            StarredRepo.repo_id == repository.id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return Response(status_code=204)


@router.put("/user/starred/{owner}/{repo}", status_code=204)
async def star_repo(owner: str, repo: str, user: AuthUser, db: DbSession):
    """Star a repository."""
    repository = await get_repo_or_404(owner, repo, db)

    existing = await db.execute(
        select(StarredRepo).where(
            StarredRepo.user_id == user.id,
            StarredRepo.repo_id == repository.id,
        )
    )
    if existing.scalar_one_or_none() is None:
        db.add(StarredRepo(user_id=user.id, repo_id=repository.id))
        repository.stargazers_count += 1
        await db.commit()

    return Response(status_code=204)


@router.delete("/user/starred/{owner}/{repo}", status_code=204)
async def unstar_repo(owner: str, repo: str, user: AuthUser, db: DbSession):
    """Unstar a repository."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        select(StarredRepo).where(
            StarredRepo.user_id == user.id,
            StarredRepo.repo_id == repository.id,
        )
    )
    star = result.scalar_one_or_none()
    if star:
        await db.delete(star)
        repository.stargazers_count = max(0, repository.stargazers_count - 1)
        await db.commit()

    return Response(status_code=204)


@router.get("/users/{username}/starred")
async def list_user_starred(
    username: str, db: DbSession, current_user: CurrentUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List repositories starred by a user."""
    result = await db.execute(select(User).where(User.login == username))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Not Found")

    query = (
        select(StarredRepo)
        .where(StarredRepo.user_id == target.id)
        .order_by(StarredRepo.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    stars = (await db.execute(query)).scalars().all()
    return [_repo_json(s.repository, BASE) for s in stars if s.repository]
