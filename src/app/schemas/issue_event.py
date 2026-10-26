from pydantic import BaseModel

from app.schemas.issue import IssueResponse
from app.schemas.user import SimpleUser


class IssueEventLabel(BaseModel):
    id: int
    name: str
    color: str
    description: str | None = None
    default: bool = False


class IssueEventResponse(BaseModel):
    id: int
    node_id: str
    url: str
    actor: SimpleUser
    event: str
    created_at: str
    label: IssueEventLabel | None = None
    commit_id: str | None = None
    commit_url: str | None = None


class TimelineSource(BaseModel):
    """What a cross-referenced event points back at."""

    type: str
    issue: IssueResponse


class TimelineItemResponse(BaseModel):
    """One entry of ``GET /repos/{owner}/{repo}/issues/{n}/timeline``.

    As on GitHub, the fields depend on ``event``: a ``commented`` entry is the
    comment with ``event`` and ``actor`` added; a ``cross-referenced`` entry
    has no id and carries ``source``; label events are as in ``/events``.
    """

    event: str
    actor: SimpleUser | None = None
    created_at: str
    updated_at: str | None = None
    id: int | None = None
    node_id: str | None = None
    url: str | None = None
    label: IssueEventLabel | None = None
    source: TimelineSource | None = None
    body: str | None = None
    user: SimpleUser | None = None
    html_url: str | None = None
    issue_url: str | None = None
    author_association: str | None = None
