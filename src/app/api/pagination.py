"""RFC 5988 ``Link`` headers for paginated list endpoints."""

from fastapi import Request


def pagination_links(request: Request, page: int, per_page: int, total: int) -> str:
    """Build a GitHub-style ``Link`` header value for one page of ``total`` items.

    Each link is the request's own URL with ``page`` replaced and ``per_page``
    set, so filters such as ``state``, ``labels`` and ``since`` carry over to
    the other pages, as on GitHub.
    """
    last_page = max(1, (total + per_page - 1) // per_page)

    def link(target: int, rel: str) -> str:
        url = request.url.include_query_params(page=target, per_page=per_page)
        return f'<{url}>; rel="{rel}"'

    parts: list[str] = []
    if page < last_page:
        parts.append(link(page + 1, "next"))
        parts.append(link(last_page, "last"))
    if page > 1:
        parts.append(link(page - 1, "prev"))
        parts.append(link(1, "first"))
    return ", ".join(parts)
