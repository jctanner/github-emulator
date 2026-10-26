"""GitHub-compatible issue event history and timeline endpoints."""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.comments import _comment_json
from app.api.deps import CurrentUser, DbSession, get_repo_or_404
from app.api.issues import _issue_json
from app.config import settings
from app.models.comment import IssueComment
from app.models.issue import Issue
from app.models.issue_event import IssueEvent
from app.schemas.issue_event import IssueEventResponse, TimelineItemResponse
from app.schemas.user import SimpleUser, _fmt_dt, _make_node_id
from app.services import repository_access
from app.services.cross_references import CROSS_REFERENCED


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


async def _issue_or_404(owner: str, repo: str, issue_number: int, db) -> Issue:
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
    return issue


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
    issue = await _issue_or_404(owner, repo, issue_number, db)
    # GitHub lists cross-references only in the timeline.
    events = (
        await db.execute(
            select(IssueEvent)
            .where(IssueEvent.issue_id == issue.id, IssueEvent.event != CROSS_REFERENCED)
            .order_by(IssueEvent.created_at, IssueEvent.id)
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    ).scalars().all()
    return [_event_response(event, owner, repo) for event in events]


@router.get(
    "/repos/{owner}/{repo}/issues/{issue_number}/timeline",
    response_model=list[TimelineItemResponse],
    response_model_exclude_none=True,
)
async def list_issue_timeline(
    owner: str,
    repo: str,
    issue_number: int,
    db: DbSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """Comments and events in order, including cross-references."""
    issue = await _issue_or_404(owner, repo, issue_number, db)
    base = settings.BASE_URL
    items: list[dict] = []

    comments = (
        await db.execute(select(IssueComment).where(IssueComment.issue_id == issue.id))
    ).scalars().all()
    for comment in comments:
        entry = _comment_json(comment, owner, repo, issue_number, base)
        entry.update(event="commented", actor=entry["user"])
        items.append(entry)

    events = (
        await db.execute(select(IssueEvent).where(IssueEvent.issue_id == issue.id))
    ).scalars().all()
    readable: dict[int, bool] = {}
    for issue_event in events:
        if issue_event.event != CROSS_REFERENCED:
            items.append(_event_response(issue_event, owner, repo).model_dump())
            continue
        source = issue_event.source_issue
        # Gone with its repository, or in one this viewer cannot see.
        if source is None or source.repository is None:
            continue
        if source.repo_id not in readable:
            readable[source.repo_id] = await repository_access.can_read(
                db, source.repository, current_user
            )
        if not readable[source.repo_id]:
            continue
        created = _fmt_dt(issue_event.created_at)
        items.append({
            "event": CROSS_REFERENCED,
            "actor": SimpleUser.from_db(issue_event.actor, base).model_dump(),
            "created_at": created,
            "updated_at": created,
            "source": {"type": "issue", "issue": _issue_json(source, base)},
        })

    items.sort(key=lambda item: item["created_at"])
    start = (page - 1) * per_page
    return items[start:start + per_page]
