"""Serve requests addressed to raw.githubusercontent.com from the raw route.

Some clients hard-code github.com's raw host instead of deriving it from the
API URL: Fullsend fetches a URL-sourced harness from
``https://raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>``. A test
machine that wants such fetches served locally resolves that name to the
emulator, trusts the emulator's certificate for that name, and sets
``RAW_CONTENT_UPSTREAM_ADDR`` to the real host's address, looked up before the
name was pointed here. The request then arrives with that Host header and the
github.com path shape. This rewrites the path to the enterprise shape the raw
route serves, ``/<owner>/<repo>/raw/<ref>/<path>``; nothing else changes, so
the same visibility check applies.

A ref may be given as ``refs/heads/<name>`` or ``refs/tags/<name>``, as on
github.com; a branch name containing a slash is not supported.

The original path is kept in the scope as ``raw_host_path``: for a
repository the emulator does not have, the raw route fetches that path from
the real host when ``RAW_CONTENT_UPSTREAM_ADDR`` is set.
"""

from app.config import settings


class RawHostMiddleware:
    """Rewrite raw.githubusercontent.com paths to the raw route."""

    def __init__(self, app):
        self.app = app
        self.hosts = {h.strip().lower() for h in settings.RAW_CONTENT_HOSTS.split(",") if h.strip()}

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self.hosts:
            host = ""
            for name, value in scope.get("headers") or ():
                if name == b"host":
                    host = value.decode("latin-1").rsplit(":", 1)[0].lower()
                    break
            if host in self.hosts:
                path = _enterprise_path(scope["path"])
                if path is not None:
                    scope = dict(scope, path=path, raw_path=path.encode(), raw_host_path=scope["path"])
        await self.app(scope, receive, send)


def _enterprise_path(path: str) -> str | None:
    parts = path.lstrip("/").split("/")
    if len(parts) >= 4 and parts[2] == "refs" and parts[3] in ("heads", "tags"):
        if len(parts) < 6:
            return None
        owner, repo, ref, rest = parts[0], parts[1], parts[4], parts[5:]
    elif len(parts) >= 4:
        owner, repo, ref, rest = parts[0], parts[1], parts[2], parts[3:]
    else:
        return None
    return "/" + "/".join([owner, repo, "raw", ref, *rest])
