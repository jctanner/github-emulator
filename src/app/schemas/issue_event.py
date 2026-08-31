from pydantic import BaseModel

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
