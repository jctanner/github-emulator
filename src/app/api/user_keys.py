"""SSH and GPG key endpoints for the authenticated user (DB-backed)."""

import base64
import hashlib
import json

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession
from app.config import settings
from app.models.ssh_key import SSHKey, GPGKey
from app.models.user import User
from app.schemas.user import _fmt_dt

router = APIRouter(tags=["user-keys"])

BASE = settings.BASE_URL


def _compute_fingerprint(key_str: str) -> str:
    """Compute MD5 fingerprint from SSH public key material."""
    try:
        parts = key_str.strip().split()
        if len(parts) >= 2:
            key_data = base64.b64decode(parts[1])
            md5 = hashlib.md5(key_data).hexdigest()
            return ":".join(md5[i:i+2] for i in range(0, len(md5), 2))
    except Exception:
        pass
    return ""


def _ssh_key_json(key: SSHKey, base_url: str) -> dict:
    api = f"{base_url}/api/v3"
    return {
        "id": key.id,
        "key": key.key,
        "url": f"{api}/user/keys/{key.id}",
        "title": key.title,
        "verified": key.verified,
        "created_at": _fmt_dt(key.created_at),
        "read_only": key.read_only,
    }


def _gpg_key_json(key: GPGKey, base_url: str) -> dict:
    api = f"{base_url}/api/v3"
    emails = []
    if key.emails:
        try:
            emails = json.loads(key.emails)
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "id": key.id,
        "key_id": key.key_id or "",
        "public_key": key.public_key,
        "name": key.name or "",
        "emails": emails,
        "can_sign": key.can_sign,
        "can_encrypt_comms": key.can_encrypt_comms,
        "can_encrypt_storage": key.can_encrypt_storage,
        "can_certify": key.can_certify,
        "created_at": _fmt_dt(key.created_at),
        "expires_at": _fmt_dt(key.expires_at),
        "raw_key": key.public_key,
        "subkeys": [],
    }


# --- SSH Keys ---

@router.get("/user/keys")
async def list_keys(user: AuthUser, db: DbSession):
    """List SSH keys for the authenticated user."""
    result = await db.execute(
        select(SSHKey).where(SSHKey.user_id == user.id).order_by(SSHKey.id)
    )
    keys = result.scalars().all()
    return [_ssh_key_json(k, BASE) for k in keys]


@router.post("/user/keys", status_code=201)
async def create_key(body: dict, user: AuthUser, db: DbSession):
    """Add an SSH key for the authenticated user."""
    title = body.get("title", "")
    key_value = body.get("key", "")
    if not key_value:
        raise HTTPException(status_code=422, detail="key is required")

    fingerprint = _compute_fingerprint(key_value)

    key = SSHKey(
        user_id=user.id,
        title=title,
        key=key_value,
        fingerprint=fingerprint,
        verified=True,
        read_only=False,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return _ssh_key_json(key, BASE)


@router.get("/user/keys/{key_id}")
async def get_key(key_id: int, user: AuthUser, db: DbSession):
    """Get an SSH key by ID."""
    result = await db.execute(
        select(SSHKey).where(SSHKey.id == key_id, SSHKey.user_id == user.id)
    )
    key = result.scalar_one_or_none()
    if key is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _ssh_key_json(key, BASE)


@router.delete("/user/keys/{key_id}", status_code=204)
async def delete_key(key_id: int, user: AuthUser, db: DbSession):
    """Delete an SSH key."""
    result = await db.execute(
        select(SSHKey).where(SSHKey.id == key_id, SSHKey.user_id == user.id)
    )
    key = result.scalar_one_or_none()
    if key is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await db.delete(key)
    await db.commit()


# --- GPG Keys ---

@router.get("/user/gpg_keys")
async def list_gpg_keys(user: AuthUser, db: DbSession):
    """List GPG keys for the authenticated user."""
    result = await db.execute(
        select(GPGKey).where(GPGKey.user_id == user.id).order_by(GPGKey.id)
    )
    keys = result.scalars().all()
    return [_gpg_key_json(k, BASE) for k in keys]


@router.post("/user/gpg_keys", status_code=201)
async def create_gpg_key(body: dict, user: AuthUser, db: DbSession):
    """Add a GPG key for the authenticated user."""
    armored_key = body.get("armored_public_key", "")
    if not armored_key:
        raise HTTPException(status_code=422, detail="armored_public_key is required")

    gpg = GPGKey(
        user_id=user.id,
        public_key=armored_key,
        key_id=body.get("key_id"),
        name=body.get("name"),
        emails=json.dumps(body.get("emails", [])),
        can_sign=True,
    )
    db.add(gpg)
    await db.commit()
    await db.refresh(gpg)
    return _gpg_key_json(gpg, BASE)


@router.get("/user/gpg_keys/{gpg_key_id}")
async def get_gpg_key(gpg_key_id: int, user: AuthUser, db: DbSession):
    """Get a GPG key by ID."""
    result = await db.execute(
        select(GPGKey).where(GPGKey.id == gpg_key_id, GPGKey.user_id == user.id)
    )
    key = result.scalar_one_or_none()
    if key is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _gpg_key_json(key, BASE)


@router.delete("/user/gpg_keys/{gpg_key_id}", status_code=204)
async def delete_gpg_key(gpg_key_id: int, user: AuthUser, db: DbSession):
    """Delete a GPG key."""
    result = await db.execute(
        select(GPGKey).where(GPGKey.id == gpg_key_id, GPGKey.user_id == user.id)
    )
    key = result.scalar_one_or_none()
    if key is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await db.delete(key)
    await db.commit()


# --- Public endpoints ---

@router.get("/users/{username}/keys")
async def list_user_public_keys(
    username: str, db: DbSession, current_user: CurrentUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List public SSH keys for a user."""
    result = await db.execute(select(User).where(User.login == username))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=404, detail="Not Found")

    query = (
        select(SSHKey)
        .where(SSHKey.user_id == target.id)
        .order_by(SSHKey.id)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    keys = (await db.execute(query)).scalars().all()
    return [{"id": k.id, "key": k.key} for k in keys]
