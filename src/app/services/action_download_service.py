"""Actions for the upstream runner: parsing `uses:` and fetching the action.

The real actions/runner receives a `uses:` step as a *repository reference*
and, before the job starts, asks the service for download info: for each
`owner/repo@ref` it wants the resolved commit and an archive URL. It then
downloads that archive with Basic auth `x-access-token:<job token>` and
extracts it under `_work/_actions`.

Two consequences shape this module. The ref has to be resolved to a commit
somewhere with a route to github.com, which is the emulator pod, not the
runner (whose egress is a deployment decision). And the archive URL must
point back at the emulator rather than at github.com: the runner sends the
*emulator's* job token as the download credential, and GitHub answers a
foreign credential with 401 before it ever serves a public tarball.

Refs that are already a full commit SHA skip the lookup, so a pinned
workflow never touches the GitHub API.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from dataclasses import dataclass

import httpx

from app.config import settings

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_RESOLVE_TTL_SECONDS = 600
_resolved: dict[tuple[str, str], tuple[float, str]] = {}


class UpstreamActionError(Exception):
    """The action could not be resolved or fetched upstream."""

    def __init__(self, message: str, *, not_found: bool = False):
        super().__init__(message)
        self.not_found = not_found


@dataclass(frozen=True)
class UsesReference:
    """A parsed `uses:` value in the runner's RepositoryPathReference shape."""

    repository_type: str  # "GitHub" or "self"
    name: str = ""  # owner/repo, empty for a local action
    ref: str = ""
    path: str = ""

    def as_step_reference(self) -> dict:
        reference = {"type": "Repository", "repositoryType": self.repository_type}
        if self.name:
            reference["name"] = self.name
        if self.ref:
            reference["ref"] = self.ref
        if self.path:
            reference["path"] = self.path
        return reference


def parse_uses(uses: str) -> UsesReference | None:
    """Mirror PipelineTemplateConverter: `./path`, or `owner/repo[/path]@ref`.

    `docker://` images and malformed values return None; the caller decides
    how loudly to fail. This is the runner's own grammar, so a value it
    would reject is rejected here too rather than reaching it half-formed.
    """
    if uses.startswith("docker://"):
        return None
    if uses.startswith("./") or uses.startswith(".\\"):
        return UsesReference(repository_type="self", path=uses)
    segments = uses.split("@")
    if len(segments) != 2 or not segments[1]:
        return None
    parts = [p for p in re.split(r"[/\\]", segments[0]) if p]
    if len(parts) < 2:
        return None
    return UsesReference(
        repository_type="GitHub",
        name=f"{parts[0]}/{parts[1]}",
        ref=segments[1],
        path="/".join(parts[2:]),
    )


def _upstream_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "github-emulator-actions",
    }
    if settings.ACTIONS_UPSTREAM_TOKEN:
        headers["Authorization"] = f"Bearer {settings.ACTIONS_UPSTREAM_TOKEN}"
    return headers


async def resolve_action_sha(name_with_owner: str, ref: str) -> str:
    """The commit `ref` points at in the upstream repository."""
    if _SHA_RE.match(ref):
        return ref
    key = (name_with_owner, ref)
    cached = _resolved.get(key)
    now = time.monotonic()
    if cached and cached[0] > now:
        return cached[1]

    url = f"{settings.ACTIONS_UPSTREAM_API_URL.rstrip('/')}/repos/{name_with_owner}/commits/{ref}"
    headers = {**_upstream_headers(), "Accept": "application/vnd.github.sha"}
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise UpstreamActionError(
            f"could not reach {settings.ACTIONS_UPSTREAM_API_URL} to resolve "
            f"{name_with_owner}@{ref}: {exc}"
        ) from exc
    if response.status_code in (404, 422):
        raise UpstreamActionError(
            f"{name_with_owner}@{ref} does not exist upstream "
            f"({response.status_code} from {url})",
            not_found=True,
        )
    if response.status_code != 200:
        raise UpstreamActionError(
            f"resolving {name_with_owner}@{ref} failed: "
            f"{response.status_code} from {url}: {response.text[:200]}"
        )
    sha = response.text.strip()
    if not _SHA_RE.match(sha):
        raise UpstreamActionError(
            f"resolving {name_with_owner}@{ref}: unexpected body from {url}: {sha[:80]!r}"
        )
    _resolved[key] = (now + _RESOLVE_TTL_SECONDS, sha)
    return sha


def archive_cache_path(name_with_owner: str, sha: str, fmt: str) -> str:
    owner, repo = name_with_owner.split("/", 1)
    return os.path.join(settings.DATA_DIR, "actions-archives", owner, repo, f"{sha}.{fmt}")


async def fetch_action_archive(name_with_owner: str, sha: str, fmt: str) -> str:
    """Path of the archive for `sha`, fetched from upstream once and kept.

    `fmt` is `tar.gz` or `zip`, the two the runner asks for by platform.
    Archives are immutable per SHA, so the cache never expires.
    """
    if fmt not in ("tar.gz", "zip"):
        raise UpstreamActionError(f"unsupported archive format {fmt!r}", not_found=True)
    path = archive_cache_path(name_with_owner, sha, fmt)
    if os.path.exists(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)

    url = f"{settings.ACTIONS_UPSTREAM_ARCHIVE_URL.rstrip('/')}/{name_with_owner}/{fmt}/{sha}"
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".partial-")
    try:
        with os.fdopen(fd, "wb") as out:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                async with client.stream("GET", url, headers=_upstream_headers()) as response:
                    if response.status_code == 404:
                        raise UpstreamActionError(
                            f"no archive for {name_with_owner}@{sha} at {url}", not_found=True
                        )
                    if response.status_code != 200:
                        raise UpstreamActionError(
                            f"fetching {name_with_owner}@{sha} failed: "
                            f"{response.status_code} from {url}"
                        )
                    async for chunk in response.aiter_bytes():
                        out.write(chunk)
        os.replace(tmp, path)
    except httpx.HTTPError as exc:
        raise UpstreamActionError(
            f"could not fetch {name_with_owner}@{sha} from {url}: {exc}"
        ) from exc
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return path
