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

from fastapi import APIRouter, HTTPException, Response

from app.api.deps import CurrentUser, DbSession, get_repo_record_or_404
from app.git.bare_repo import get_file_content, resolve_ref
from app.services import repository_access

router = APIRouter(tags=["raw"])


@router.get("/{owner}/{repo}/raw/{ref}/{path:path}")
async def raw_file(
    owner: str,
    repo: str,
    ref: str,
    path: str,
    db: DbSession,
    current_user: CurrentUser,
) -> Response:
    """Serve one file's bytes at a ref."""
    repository = await get_repo_record_or_404(owner, repo, db)

    # This route sits outside /repos/, so the visibility check wrapped around
    # authentication does not cover it. Ask the same question here rather than
    # leaving a second door into a private repository.
    if not await repository_access.can_read(db, repository, current_user):
        raise HTTPException(status_code=404, detail="Not Found")

    if not repository.disk_path:
        raise HTTPException(status_code=404, detail="Not Found")

    resolved = await resolve_ref(repository.disk_path, ref) or ref
    content = await get_file_content(repository.disk_path, resolved, path)
    if content is None:
        raise HTTPException(status_code=404, detail="Not Found")

    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={"X-Content-Type-Options": "nosniff"},
    )
