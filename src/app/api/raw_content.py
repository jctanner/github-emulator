"""Raw file content, the way an enterprise GitHub host serves it.

github.com puts raw file bytes on a separate hostname,
``raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>``. An enterprise
install has no second hostname and serves the same bytes from the appliance
itself, at ``<host>/<owner>/<repo>/raw/<ref>/<path>``. This emulator is the
second shape, so it implements that route.

It exists because tools fetch files this way rather than through the contents
API: Fullsend resolves an agent definition to a commit and then downloads the
file from a raw URL. Without this route such a tool has nowhere local to look
and reaches the public internet instead.

The response is deliberately plain bytes with ``text/plain``, like the real
thing, rather than the contents API's JSON envelope.
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, get_repo_record_or_404
from app.config import settings
from app.git.bare_repo import read_object
from app.models.repository import Repository
from app.services import repository_access

router = APIRouter(tags=["raw"])
logger = logging.getLogger("github_emulator.raw")
_UPSTREAM_HOST = "raw.githubusercontent.com"


async def _from_upstream(path: str) -> Response:
    """Fetch ``path`` from the real raw.githubusercontent.com.

    ``path`` is percent-encoded, as the request carried it, and has no query
    string. The name resolves to this emulator on this machine, so connect to
    the address recorded before that, and present the real name for TLS (SNI
    and certificate check against certifi's bundle) and in Host. Proxy and
    certificate settings from the environment are not used (``trust_env``
    off), so they cannot redirect the connection. No credentials are
    forwarded. A redirect, or a body over ``RAW_CONTENT_UPSTREAM_MAX_BYTES``,
    is answered with 502.
    """
    url = f"https://{settings.RAW_CONTENT_UPSTREAM_ADDR}{path}"
    limit = settings.RAW_CONTENT_UPSTREAM_MAX_BYTES
    try:
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
            async with client.stream(
                "GET", url, headers={"Host": _UPSTREAM_HOST},
                extensions={"sni_hostname": _UPSTREAM_HOST},
            ) as upstream:
                logger.info("raw upstream %s -> %d", path, upstream.status_code)
                if 300 <= upstream.status_code < 400:
                    # Passing it on without following it would leave the
                    # client with a Location on the real host, or none.
                    raise HTTPException(status_code=502, detail="Bad Gateway")
                body = bytearray()
                async for chunk in upstream.aiter_bytes():
                    body += chunk
                    if len(body) > limit:
                        logger.warning("raw upstream %s is over %d bytes", path, limit)
                        raise HTTPException(status_code=502, detail="Bad Gateway")
    except httpx.HTTPError as exc:
        logger.warning("raw upstream fetch of %s failed: %s", path, exc)
        raise HTTPException(status_code=502, detail="Bad Gateway") from None
    return Response(
        content=bytes(body),
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "text/plain; charset=utf-8"),
        headers={"X-Content-Type-Options": "nosniff"},
    )


async def _has_repository_named(db, full_name: str) -> bool:
    """Whether the emulator has a repository by this name, in any letter case."""
    result = await db.execute(
        select(Repository.id).where(func.lower(Repository.full_name) == full_name.lower()).limit(1)
    )
    return result.first() is not None


@router.get("/{owner}/{repo}/raw/{ref}/{path:path}")
async def raw_file(
    owner: str,
    repo: str,
    ref: str,
    path: str,
    request: Request,
    db: DbSession,
    current_user: CurrentUser,
) -> Response:
    """Serve one file's bytes at a ref."""
    try:
        repository = await get_repo_record_or_404(owner, repo, db)
    except HTTPException:
        # Not one of the emulator's repositories: on the raw host, the real
        # one may have it. A different spelling of one of the emulator's own
        # names (GitHub names are case-insensitive) is not fetched upstream.
        upstream_path = request.scope.get("raw_host_path")
        if (
            upstream_path
            and settings.RAW_CONTENT_UPSTREAM_ADDR
            and not await _has_repository_named(db, f"{owner}/{repo}")
        ):
            return await _from_upstream(upstream_path)
        raise

    # This route sits outside /repos/, so the visibility check wrapped around
    # authentication does not cover it. Ask the same question here rather than
    # leaving a second door into a private repository.
    if not await repository_access.can_read(db, repository, current_user):
        raise HTTPException(status_code=404, detail="Not Found")

    if not repository.disk_path:
        raise HTTPException(status_code=404, detail="Not Found")

    # "<ref>:<path>" resolves the ref the way rev-parse does, in the same
    # process that reads the file; git reads the name from stdin. Like
    # GitHub's raw host, anything but a file (a directory, a submodule) is 404.
    # A raw-host URL that named refs/heads/<x> or refs/tags/<x> reads exactly
    # that ref; a short name resolves tags before branches.
    ref = request.scope.get("raw_host_ref") or ref
    found = await read_object(repository.disk_path, f"{ref}:{path}")
    if found is None or found[1] != "blob":
        raise HTTPException(status_code=404, detail="Not Found")

    return Response(
        content=found[2],
        media_type="text/plain; charset=utf-8",
        headers={"X-Content-Type-Options": "nosniff"},
    )
