"""GitHub-compatible issue event history endpoints."""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.issue import Issue
from app.models.issue_event import IssueEvent
from app.schemas.issue_event import IssueEventResponse
from app.schemas.user import SimpleUser, _fmt_dt, _make_node_id


router = APIRouter(tags=["issue-events"])


def _event_response(event: IssueEvent, owner: str, repo: str) -> IssueEventResponse:
    api = f"{settings.BASE_URL}/api/v3/repos/{owner}/{repo}"
    return IssueEventResponse(
        id=event.id,
        node_id=_make_node_id("IssueEvent", event.id),
        url=f"{api}/issues/events/{event.id}",
        actor=SimpleUser.from_db(event.actor, settings.BASE_URL),
        event=event.event,
        created_at=_fmt_dt(event.created_at),
        label=event.label,
    )


@router.get(
    "/repos/{owner}/{repo}/issues/{issue_number}/events",
    response_model=list[IssueEventResponse],
)
async def list_issue_events(
    owner: str,
    repo: str,
    issue_number: int,
    db: DbSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    repository = await get_repo_or_404(owner, repo, db)
    issue = (
        await db.execute(
            select(Issue).where(
                Issue.repo_id == repository.id,
                Issue.number == issue_number,
            )
        )
    ).scalar_one_or_none()
    if issue is None:
        raise HTTPException(status_code=404, detail="Not Found")

    events = (
        await db.execute(
            select(IssueEvent)
            .where(IssueEvent.issue_id == issue.id)
            .order_by(IssueEvent.created_at, IssueEvent.id)
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).scalars().all()
    return [_event_response(event, owner, repo) for event in events]
