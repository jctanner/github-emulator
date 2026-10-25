"""RFC 5988 ``Link`` headers for paginated list endpoints."""

from urllib.parse import urlsplit

from fastapi import Request

from app.config import settings


def _public_url(request: Request):
    """The request's URL on the configured public base URL.

    An ingress may deliver the request with an internal ``Host`` (on
    breadboard, the in-cluster service name). Response bodies use
    ``settings.BASE_URL``, and links must too, or clients outside the
    cluster cannot follow them. The path and query stay the request's own.
    """
    base = urlsplit(settings.BASE_URL)
    url = request.url
    if not base.netloc:
        return url
    path = url.path
    prefix = base.path.rstrip("/")
    if prefix and not path.startswith(prefix + "/"):
        path = prefix + path
    return url.replace(scheme=base.scheme, netloc=base.netloc, path=path)


def pagination_links(request: Request, page: int, per_page: int, total: int) -> str:
    """Build a GitHub-style ``Link`` header value for one page of ``total`` items.

    Each link is the request's own URL on the public base URL, with ``page``
    replaced and ``per_page`` set, so filters such as ``state``, ``labels``
    and ``since`` carry over to the other pages, as on GitHub.
    """
    last_page = max(1, (total + per_page - 1) // per_page)
    public = _public_url(request)

    def link(target: int, rel: str) -> str:
        url = public.include_query_params(page=target, per_page=per_page)
        return f'<{url}>; rel="{rel}"'

    parts: list[str] = []
    if page < last_page:
        parts.append(link(page + 1, "next"))
        parts.append(link(last_page, "last"))
    if page > 1:
        parts.append(link(page - 1, "prev"))
        parts.append(link(1, "first"))
    return ", ".join(parts)
