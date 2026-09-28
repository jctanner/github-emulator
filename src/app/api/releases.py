"""Release endpoints -- CRUD and asset management.

Assets follow the artifact pattern: the database row indexes the upload and
the filesystem under DATA_DIR holds the bytes, so they survive a restart.
Downloading an asset is the API asset URL with ``Accept:
application/octet-stream``, which is what ``gh release download`` sends; the
browser URL is served too, root-mounted, for links that copy GitHub's.
"""

import os
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.api.git_refs import _git
from app.config import settings
from app.models.release import Release, ReleaseAsset
from app.schemas.user import SimpleUser, _fmt_dt, _make_node_id

router = APIRouter(tags=["releases"])
# Mounted at the root: GitHub serves browser downloads from the web host,
# not from the API, and a client following browser_download_url expects the
# path GitHub would print.
download_router = APIRouter(tags=["releases"])


def _base() -> str:
    # Read at call time: the setting is overridden after import in tests, and
    # the browser URL has to name the same host the API answered on.
    return settings.BASE_URL


def _asset_path(asset_id: int) -> str:
    return os.path.join(settings.DATA_DIR, "release-assets", str(asset_id))


def _browser_download_url(owner: str, repo_name: str, tag: str, name: str) -> str:
    return f"{_base()}/{owner}/{repo_name}/releases/download/{tag}/{name}"


def _asset_json(a: ReleaseAsset, owner: str, repo_name: str, base_url: str) -> dict:
    api = f"{base_url}/api/v3"
    uploader = SimpleUser.from_db(a.uploader, base_url).model_dump() if a.uploader else None
    return {
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
    }


async def _get_release(db, repository, release_id: int) -> Release:
    result = await db.execute(
        select(Release).where(Release.id == release_id, Release.repo_id == repository.id)
    )
    release = result.scalar_one_or_none()
    if release is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return release


async def _get_asset(db, repository, asset_id: int) -> ReleaseAsset:
    result = await db.execute(
        select(ReleaseAsset)
        .join(Release, ReleaseAsset.release_id == Release.id)
        .where(ReleaseAsset.id == asset_id, Release.repo_id == repository.id)
    )
    asset = result.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return asset


def _remove_asset_files(release: Release) -> None:
    for asset in release.assets or []:
        try:
            os.remove(_asset_path(asset.id))
        except FileNotFoundError:
            pass


async def _ensure_tag(repository, tag_name: str, target: str) -> None:
    """Create the release's tag when the repository does not have it.

    GitHub creates a lightweight tag at ``target_commitish`` on release
    creation; an install that resolves a commit to a release tag through the
    tags API depends on the tag existing, not only the release row.
    """
    if not repository.disk_path or not os.path.isdir(repository.disk_path):
        return
    try:
        await _git(repository.disk_path, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag_name}")
        return
    except RuntimeError:
        pass
    try:
        sha = (await _git(repository.disk_path, "rev-parse", "--verify", f"{target}^{{commit}}")).strip()
    except RuntimeError:
        raise HTTPException(
            status_code=422,
            detail=f"target_commitish {target!r} does not resolve to a commit",
        )
    await _git(repository.disk_path, "update-ref", f"refs/tags/{tag_name}", sha)


def _release_json(release: Release, owner: str, repo_name: str, base_url: str) -> dict:
    api = f"{base_url}/api/v3"
    author = SimpleUser.from_db(release.author, base_url).model_dump() if release.author else None

    assets = [_asset_json(a, owner, repo_name, base_url) for a in (release.assets or [])]

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
        .order_by(Release.created_at.desc(), Release.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    releases = (await db.execute(query)).scalars().all()
    return [_release_json(r, owner, repo, _base()) for r in releases]


@router.post("/repos/{owner}/{repo}/releases", status_code=201)
async def create_release(
    owner: str, repo: str, body: dict, user: AuthUser, db: DbSession
):
    """Create a release."""
    repository = await get_repo_or_404(owner, repo, db)

    tag_name = body.get("tag_name")
    if not tag_name:
        raise HTTPException(status_code=422, detail="tag_name is required")

    target_commitish = body.get("target_commitish") or repository.default_branch
    if not body.get("draft"):
        await _ensure_tag(repository, tag_name, target_commitish)

    now = datetime.now(timezone.utc)
    release = Release(
        repo_id=repository.id,
        tag_name=tag_name,
        target_commitish=target_commitish,
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
    return _release_json(release, owner, repo, _base())


@router.get("/repos/{owner}/{repo}/releases/latest")
async def get_latest_release(
    owner: str, repo: str, db: DbSession, current_user: CurrentUser
):
    """Get the latest release."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Release)
        .where(Release.repo_id == repository.id, Release.draft == False)
        # Newest first; the id breaks ties inside one clock second.
        .order_by(Release.created_at.desc(), Release.id.desc())
        .limit(1)
    )
    release = result.scalar_one_or_none()
    if release is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _release_json(release, owner, repo, _base())


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
    return _release_json(release, owner, repo, _base())


@router.get("/repos/{owner}/{repo}/releases/assets/{asset_id}")
async def get_release_asset(
    owner: str, repo: str, asset_id: int, request: Request, db: DbSession, current_user: CurrentUser
):
    """Get an asset, or its bytes when the client accepts an octet stream."""
    repository = await get_repo_or_404(owner, repo, db)
    asset = await _get_asset(db, repository, asset_id)
    if "application/octet-stream" in request.headers.get("accept", ""):
        return await _serve_asset(db, asset)
    return _asset_json(asset, owner, repo, _base())


async def _serve_asset(db, asset: ReleaseAsset):
    path = _asset_path(asset.id)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Asset content is missing")
    asset.download_count = (asset.download_count or 0) + 1
    await db.commit()
    return FileResponse(path, media_type=asset.content_type, filename=asset.name)


@router.patch("/repos/{owner}/{repo}/releases/assets/{asset_id}")
async def update_release_asset(
    owner: str, repo: str, asset_id: int, body: dict, user: AuthUser, db: DbSession
):
    """Rename or relabel an asset."""
    repository = await get_repo_or_404(owner, repo, db)
    asset = await _get_asset(db, repository, asset_id)
    if "name" in body and body["name"]:
        asset.name = str(body["name"])
        asset.browser_download_url = _browser_download_url(owner, repo, asset.release.tag_name, asset.name)
    if "label" in body:
        asset.label = body["label"]
    await db.commit()
    await db.refresh(asset)
    return _asset_json(asset, owner, repo, _base())


@router.delete("/repos/{owner}/{repo}/releases/assets/{asset_id}", status_code=204)
async def delete_release_asset(
    owner: str, repo: str, asset_id: int, user: AuthUser, db: DbSession
):
    """Delete an asset and its bytes."""
    repository = await get_repo_or_404(owner, repo, db)
    asset = await _get_asset(db, repository, asset_id)
    try:
        os.remove(_asset_path(asset.id))
    except FileNotFoundError:
        pass
    await db.delete(asset)
    await db.commit()


@router.get("/repos/{owner}/{repo}/releases/{release_id}/assets")
async def list_release_assets(
    owner: str, repo: str, release_id: int, db: DbSession, current_user: CurrentUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List a release's assets."""
    repository = await get_repo_or_404(owner, repo, db)
    release = await _get_release(db, repository, release_id)
    assets = sorted(release.assets or [], key=lambda a: a.id)
    window = assets[(page - 1) * per_page:page * per_page]
    return [_asset_json(a, owner, repo, _base()) for a in window]


@router.post("/repos/{owner}/{repo}/releases/{release_id}/assets", status_code=201)
async def upload_release_asset(
    owner: str, repo: str, release_id: int, request: Request, user: AuthUser, db: DbSession,
    name: str = Query(""),
    label: str | None = Query(None),
):
    """Upload an asset: the raw request body, named by the query string.

    GitHub serves this from uploads.github.com; the release's ``upload_url``
    here points at this host, so a client that follows it lands here.
    """
    repository = await get_repo_or_404(owner, repo, db)
    release = await _get_release(db, repository, release_id)
    name = os.path.basename(name.strip())
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    if any(a.name == name for a in release.assets or []):
        # GitHub's shape for a duplicate: 422 with an already_exists error.
        raise HTTPException(
            status_code=422,
            detail={"message": "Validation Failed", "errors": [
                {"resource": "ReleaseAsset", "code": "already_exists", "field": "name"},
            ]},
        )
    content = await request.body()
    content_type = request.headers.get("content-type") or "application/octet-stream"
    asset = ReleaseAsset(
        release_id=release.id,
        name=name,
        label=label,
        content_type=content_type.split(";", 1)[0].strip(),
        size=len(content),
        uploader_id=user.id,
        browser_download_url=_browser_download_url(owner, repo, release.tag_name, name),
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    path = _asset_path(asset.id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(content)
    except Exception:
        # Do not leave a row pointing at bytes that were never written.
        await db.delete(asset)
        await db.commit()
        raise
    await db.refresh(release)
    return _asset_json(asset, owner, repo, _base())


@download_router.get("/{owner}/{repo}/releases/download/{tag}/{name}")
async def browser_download_asset(
    owner: str, repo: str, tag: str, name: str, db: DbSession, current_user: CurrentUser
):
    """The browser download URL: the asset's bytes by release tag and name."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(ReleaseAsset)
        .join(Release, ReleaseAsset.release_id == Release.id)
        .where(Release.repo_id == repository.id, Release.tag_name == tag, ReleaseAsset.name == name)
    )
    asset = result.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return await _serve_asset(db, asset)


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
    return _release_json(release, owner, repo, _base())


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
    return _release_json(release, owner, repo, _base())


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
    _remove_asset_files(release)
    await db.delete(release)
    await db.commit()
