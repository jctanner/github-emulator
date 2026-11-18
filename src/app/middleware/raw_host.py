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
github.com; a branch name containing a slash is not supported. The fully
qualified ref is kept in the scope as ``raw_host_ref``, so the raw route reads
that exact ref: a branch and a tag of the same name each serve their own file.

The original path, percent-encoded as it arrived, is kept in the scope as
``raw_host_path``: for a repository the emulator does not have, the raw route
fetches that path from the real host when ``RAW_CONTENT_UPSTREAM_ADDR`` is
set.
"""

from urllib.parse import quote

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
                rewritten = _enterprise_path(scope["path"])
                if rewritten is not None:
                    path, explicit_ref = rewritten
                    scope = dict(
                        scope,
                        path=path,
                        raw_path=quote(path, safe="/").encode("ascii"),
                        raw_host_path=_encoded_path(scope),
                    )
                    if explicit_ref is not None:
                        scope["raw_host_ref"] = explicit_ref
        await self.app(scope, receive, send)


def _encoded_path(scope) -> str:
    """The request path as it arrived, still percent-encoded, without a query.

    A filename with ``#``, ``?`` or ``%`` in it stays a filename when the path
    is sent on to the real host.
    """
    raw = scope.get("raw_path")
    if raw:
        text = raw.decode("latin-1").split("?", 1)[0]
        if text.startswith("/") and "#" not in text:
            return text
    return quote(scope["path"], safe="/")


def _enterprise_path(path: str) -> tuple[str, str | None] | None:
    """The raw route's path, and the fully qualified ref if the URL gave one."""
    parts = path.lstrip("/").split("/")
    explicit_ref = None
    if len(parts) >= 4 and parts[2] == "refs" and parts[3] in ("heads", "tags"):
        if len(parts) < 6:
            return None
        owner, repo, ref, rest = parts[0], parts[1], parts[4], parts[5:]
        explicit_ref = f"refs/{parts[3]}/{ref}"
    elif len(parts) >= 4:
        owner, repo, ref, rest = parts[0], parts[1], parts[2], parts[3:]
    else:
        return None
    return "/" + "/".join([owner, repo, "raw", ref, *rest]), explicit_ref
