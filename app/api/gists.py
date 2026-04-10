"""Gist endpoints -- CRUD for gists."""

import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession
from app.config import settings
from app.models.gist import Gist, GistFile
from app.schemas.user import SimpleUser, _fmt_dt

router = APIRouter(tags=["gists"])

BASE = settings.BASE_URL


def _gist_json(gist: Gist, base_url: str) -> dict:
    api = f"{base_url}/api/v3"
    owner = SimpleUser.from_db(gist.user, base_url).model_dump() if gist.user else None

    files = {}
    if gist.files:
        for f in gist.files:
            files[f.filename] = {
                "filename": f.filename,
                "type": "text/plain",
                "language": f.language,
                "raw_url": f.raw_url or f"{api}/gists/{gist.id}/raw/{f.filename}",
                "size": f.size,
                "content": f.content,
            }

    return {
        "url": f"{api}/gists/{gist.id}",
        "forks_url": f"{api}/gists/{gist.id}/forks",
        "commits_url": f"{api}/gists/{gist.id}/commits",
        "id": gist.id,
        "node_id": "",
        "git_pull_url": f"git://{base_url.split('://', 1)[-1]}/{gist.id}.git",
        "git_push_url": f"git@{base_url.split('://', 1)[-1]}:{gist.id}.git",
        "html_url": f"{base_url}/{gist.id}",
        "files": files,
        "public": gist.public,
        "created_at": _fmt_dt(gist.created_at),
        "updated_at": _fmt_dt(gist.updated_at),
        "description": gist.description,
        "comments": 0,
        "user": None,
        "comments_url": f"{api}/gists/{gist.id}/comments",
        "owner": owner,
        "truncated": False,
    }


@router.get("/gists")
async def list_gists(
    db: DbSession, current_user: CurrentUser,
    since: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List public gists."""
    query = (
        select(Gist)
        .where(Gist.public == True)
        .order_by(Gist.updated_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    gists = (await db.execute(query)).scalars().all()
    return [_gist_json(g, BASE) for g in gists]


@router.get("/gists/public")
async def list_public_gists(
    db: DbSession,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List public gists."""
    query = (
        select(Gist)
        .where(Gist.public == True)
        .order_by(Gist.updated_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    gists = (await db.execute(query)).scalars().all()
    return [_gist_json(g, BASE) for g in gists]


@router.get("/users/{username}/gists")
async def list_user_gists(
    username: str, db: DbSession, current_user: CurrentUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List gists for a user."""
    from app.models.user import User

    result = await db.execute(select(User).where(User.login == username))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="Not Found")

    query = (
        select(Gist)
        .where(Gist.user_id == user.id, Gist.public == True)
        .order_by(Gist.updated_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    gists = (await db.execute(query)).scalars().all()
    return [_gist_json(g, BASE) for g in gists]


@router.post("/gists", status_code=201)
async def create_gist(body: dict, user: AuthUser, db: DbSession):
    """Create a gist."""
    gist_id = uuid.uuid4().hex[:20]

    gist = Gist(
        id=gist_id,
        user_id=user.id,
        description=body.get("description"),
        public=body.get("public", True),
    )
    db.add(gist)
    await db.flush()

    files = body.get("files", {})
    for filename, file_data in files.items():
        content = file_data.get("content", "")
        gist_file = GistFile(
            gist_id=gist.id,
            filename=filename,
            content=content,
            size=len(content),
        )
        db.add(gist_file)

    await db.commit()
    await db.refresh(gist)
    return _gist_json(gist, BASE)


@router.get("/gists/{gist_id}")
async def get_gist(gist_id: str, db: DbSession, current_user: CurrentUser):
    """Get a gist."""
    result = await db.execute(select(Gist).where(Gist.id == gist_id))
    gist = result.scalar_one_or_none()
    if gist is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _gist_json(gist, BASE)


@router.patch("/gists/{gist_id}")
async def update_gist(gist_id: str, body: dict, user: AuthUser, db: DbSession):
    """Update a gist."""
    result = await db.execute(select(Gist).where(Gist.id == gist_id))
    gist = result.scalar_one_or_none()
    if gist is None:
        raise HTTPException(status_code=404, detail="Not Found")

    if "description" in body:
        gist.description = body["description"]

    files = body.get("files", {})
    for filename, file_data in files.items():
        if file_data is None:
            # Delete file
            result2 = await db.execute(
                select(GistFile).where(
                    GistFile.gist_id == gist.id, GistFile.filename == filename
                )
            )
            existing = result2.scalar_one_or_none()
            if existing:
                await db.delete(existing)
        else:
            content = file_data.get("content", "")
            result2 = await db.execute(
                select(GistFile).where(
                    GistFile.gist_id == gist.id, GistFile.filename == filename
                )
            )
            existing = result2.scalar_one_or_none()
            if existing:
                existing.content = content
                existing.size = len(content)
            else:
                db.add(GistFile(
                    gist_id=gist.id,
                    filename=filename,
                    content=content,
                    size=len(content),
                ))

    await db.commit()
    await db.refresh(gist)
    return _gist_json(gist, BASE)


@router.delete("/gists/{gist_id}", status_code=204)
async def delete_gist(gist_id: str, user: AuthUser, db: DbSession):
    """Delete a gist."""
    result = await db.execute(select(Gist).where(Gist.id == gist_id))
    gist = result.scalar_one_or_none()
    if gist is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await db.delete(gist)
    await db.commit()
