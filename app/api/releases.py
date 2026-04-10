"""Release endpoints -- CRUD and asset management."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.release import Release, ReleaseAsset
from app.schemas.user import SimpleUser, _fmt_dt, _make_node_id

router = APIRouter(tags=["releases"])

BASE = settings.BASE_URL


def _release_json(release: Release, owner: str, repo_name: str, base_url: str) -> dict:
    api = f"{base_url}/api/v3"
    author = SimpleUser.from_db(release.author, base_url).model_dump() if release.author else None

    assets = []
    if release.assets:
        for a in release.assets:
            uploader = SimpleUser.from_db(a.uploader, base_url).model_dump() if a.uploader else None
            assets.append({
                "url": f"{api}/repos/{owner}/{repo_name}/releases/assets/{a.id}",
                "id": a.id,
                "node_id": _make_node_id("ReleaseAsset", a.id),
                "name": a.name,
                "label": a.label,
                "uploader": uploader,
                "content_type": a.content_type,
                "state": a.state,
                "size": a.size,
                "download_count": a.download_count,
                "created_at": _fmt_dt(a.created_at),
                "updated_at": _fmt_dt(a.updated_at),
                "browser_download_url": a.browser_download_url,
            })

    return {
        "url": f"{api}/repos/{owner}/{repo_name}/releases/{release.id}",
        "assets_url": f"{api}/repos/{owner}/{repo_name}/releases/{release.id}/assets",
        "upload_url": f"{api}/repos/{owner}/{repo_name}/releases/{release.id}/assets{{?name,label}}",
        "html_url": f"{base_url}/{owner}/{repo_name}/releases/tag/{release.tag_name}",
        "id": release.id,
        "node_id": _make_node_id("Release", release.id),
        "tag_name": release.tag_name,
        "target_commitish": release.target_commitish,
        "name": release.name,
        "draft": release.draft,
        "prerelease": release.prerelease,
        "created_at": _fmt_dt(release.created_at),
        "published_at": _fmt_dt(release.published_at),
        "author": author,
        "assets": assets,
        "tarball_url": f"{api}/repos/{owner}/{repo_name}/tarball/{release.tag_name}",
        "zipball_url": f"{api}/repos/{owner}/{repo_name}/zipball/{release.tag_name}",
        "body": release.body,
    }


@router.get("/repos/{owner}/{repo}/releases")
async def list_releases(
    owner: str, repo: str, db: DbSession, current_user: CurrentUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List releases."""
    repository = await get_repo_or_404(owner, repo, db)
    query = (
        select(Release)
        .where(Release.repo_id == repository.id)
        .order_by(Release.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    releases = (await db.execute(query)).scalars().all()
    return [_release_json(r, owner, repo, BASE) for r in releases]


@router.post("/repos/{owner}/{repo}/releases", status_code=201)
async def create_release(
    owner: str, repo: str, body: dict, user: AuthUser, db: DbSession
):
    """Create a release."""
    repository = await get_repo_or_404(owner, repo, db)

    tag_name = body.get("tag_name")
    if not tag_name:
        raise HTTPException(status_code=422, detail="tag_name is required")

    now = datetime.now(timezone.utc)
    release = Release(
        repo_id=repository.id,
        tag_name=tag_name,
        target_commitish=body.get("target_commitish", repository.default_branch),
        name=body.get("name"),
        body=body.get("body"),
        draft=body.get("draft", False),
        prerelease=body.get("prerelease", False),
        author_id=user.id,
        published_at=None if body.get("draft") else now,
    )
    db.add(release)
    await db.commit()
    await db.refresh(release)
    return _release_json(release, owner, repo, BASE)


@router.get("/repos/{owner}/{repo}/releases/{release_id}")
async def get_release(
    owner: str, repo: str, release_id: int, db: DbSession, current_user: CurrentUser
):
    """Get a release."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Release).where(
            Release.id == release_id, Release.repo_id == repository.id
        )
    )
    release = result.scalar_one_or_none()
    if release is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _release_json(release, owner, repo, BASE)


@router.get("/repos/{owner}/{repo}/releases/latest")
async def get_latest_release(
    owner: str, repo: str, db: DbSession, current_user: CurrentUser
):
    """Get the latest release."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Release)
        .where(Release.repo_id == repository.id, Release.draft == False)
        .order_by(Release.created_at.desc())
        .limit(1)
    )
    release = result.scalar_one_or_none()
    if release is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _release_json(release, owner, repo, BASE)


@router.get("/repos/{owner}/{repo}/releases/tags/{tag}")
async def get_release_by_tag(
    owner: str, repo: str, tag: str, db: DbSession, current_user: CurrentUser
):
    """Get a release by tag name."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Release).where(
            Release.repo_id == repository.id, Release.tag_name == tag
        )
    )
    release = result.scalar_one_or_none()
    if release is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _release_json(release, owner, repo, BASE)


@router.patch("/repos/{owner}/{repo}/releases/{release_id}")
async def update_release(
    owner: str, repo: str, release_id: int, body: dict, user: AuthUser, db: DbSession
):
    """Update a release."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Release).where(
            Release.id == release_id, Release.repo_id == repository.id
        )
    )
    release = result.scalar_one_or_none()
    if release is None:
        raise HTTPException(status_code=404, detail="Not Found")

    for key in ("tag_name", "target_commitish", "name", "body", "draft", "prerelease"):
        if key in body:
            setattr(release, key, body[key])

    await db.commit()
    await db.refresh(release)
    return _release_json(release, owner, repo, BASE)


@router.delete("/repos/{owner}/{repo}/releases/{release_id}", status_code=204)
async def delete_release(
    owner: str, repo: str, release_id: int, user: AuthUser, db: DbSession
):
    """Delete a release."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Release).where(
            Release.id == release_id, Release.repo_id == repository.id
        )
    )
    release = result.scalar_one_or_none()
    if release is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await db.delete(release)
    await db.commit()
