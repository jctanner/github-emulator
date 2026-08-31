"""Label endpoints -- repo labels and issue labels."""

import json
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, delete as sa_delete

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.label import Label
from app.models.issue import Issue, IssueLabel
from app.schemas.label import LabelCreate, LabelResponse, LabelUpdate
from app.services.issue_event_service import record_label_event

router = APIRouter(tags=["labels"])

BASE = settings.BASE_URL

LABEL_NAMES_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "properties": {
                        "labels": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["labels"],
                }
            }
        },
    }
}


async def _read_label_names(request: Request) -> list[str]:
    """Read labels from GitHub JSON and gh-api form-style request bodies."""
    raw = await request.body()
    if not raw:
        return []

    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return []
        names = payload.get("labels", []) if isinstance(payload, dict) else []
        return [names] if isinstance(names, str) else list(names or [])

    fields = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
    return fields.get("labels[]", []) + fields.get("labels", [])


async def _get_or_create_label(db, repository_id: int, name: str) -> Label:
    """Resolve a repository label, creating it like GitHub's issue API does."""
    result = await db.execute(
        select(Label).where(Label.repo_id == repository_id, Label.name == name)
    )
    label = result.scalar_one_or_none()
    if label is not None:
        return label

    label = Label(repo_id=repository_id, name=name, color="ededed")
    db.add(label)
    await db.flush()
    return label


async def _dispatch_label_event(db, repository, user, issue, action, label):
    from app.services.workflow_service import build_activity_payload, dispatch_event

    event = "pull_request_target" if issue.pull_request is not None else "issues"
    await dispatch_event(
        db,
        repository,
        user,
        event,
        action,
        build_activity_payload(
            repository,
            user,
            action,
            issue=issue,
            pull_request=issue.pull_request,
            label=label,
        ),
    )


# ---------------------------------------------------------------------------
# Repo-level label CRUD
# ---------------------------------------------------------------------------

@router.get("/repos/{owner}/{repo}/labels", response_model=list[LabelResponse])
async def list_labels(
    owner: str,
    repo: str,
    db: DbSession,
    current_user: CurrentUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List all labels for a repository."""
    repository = await get_repo_or_404(owner, repo, db)
    query = (
        select(Label)
        .where(Label.repo_id == repository.id)
        .order_by(Label.name)
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    labels = (await db.execute(query)).scalars().all()
    return [LabelResponse.from_db(l, BASE, owner, repo) for l in labels]


@router.post(
    "/repos/{owner}/{repo}/labels", status_code=201, response_model=LabelResponse
)
async def create_label(
    owner: str, repo: str, body: LabelCreate, user: AuthUser, db: DbSession
):
    """Create a label."""
    repository = await get_repo_or_404(owner, repo, db)

    existing = await db.execute(
        select(Label).where(
            Label.repo_id == repository.id, Label.name == body.name
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=422, detail="Label already exists")

    label = Label(
        repo_id=repository.id,
        name=body.name,
        color=body.color.lstrip("#"),
        description=body.description,
    )
    db.add(label)
    await db.commit()
    await db.refresh(label)
    return LabelResponse.from_db(label, BASE, owner, repo)


@router.get(
    "/repos/{owner}/{repo}/labels/{name}",
    response_model=LabelResponse,
)
async def get_label(
    owner: str, repo: str, name: str, db: DbSession, current_user: CurrentUser
):
    """Get a single label."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        select(Label).where(
            Label.repo_id == repository.id, Label.name == name
        )
    )
    label = result.scalar_one_or_none()
    if label is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return LabelResponse.from_db(label, BASE, owner, repo)


@router.patch(
    "/repos/{owner}/{repo}/labels/{name}", response_model=LabelResponse
)
async def update_label(
    owner: str, repo: str, name: str, body: LabelUpdate, user: AuthUser, db: DbSession
):
    """Update a label."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        select(Label).where(
            Label.repo_id == repository.id, Label.name == name
        )
    )
    label = result.scalar_one_or_none()
    if label is None:
        raise HTTPException(status_code=404, detail="Not Found")

    if body.new_name is not None:
        label.name = body.new_name
    if body.color is not None:
        label.color = body.color.lstrip("#")
    if body.description is not None:
        label.description = body.description

    await db.commit()
    await db.refresh(label)
    return LabelResponse.from_db(label, BASE, owner, repo)


@router.delete("/repos/{owner}/{repo}/labels/{name}", status_code=204)
async def delete_label(
    owner: str, repo: str, name: str, user: AuthUser, db: DbSession
):
    """Delete a label."""
    repository = await get_repo_or_404(owner, repo, db)

    result = await db.execute(
        select(Label).where(
            Label.repo_id == repository.id, Label.name == name
        )
    )
    label = result.scalar_one_or_none()
    if label is None:
        raise HTTPException(status_code=404, detail="Not Found")

    await db.delete(label)
    await db.commit()


# ---------------------------------------------------------------------------
# Issue-level label management
# ---------------------------------------------------------------------------

@router.get(
    "/repos/{owner}/{repo}/issues/{issue_number}/labels",
    response_model=list[LabelResponse],
)
async def list_issue_labels(
    owner: str, repo: str, issue_number: int, db: DbSession, current_user: CurrentUser
):
    """List labels on an issue."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Issue).where(
            Issue.repo_id == repository.id, Issue.number == issue_number
        )
    )
    issue = result.scalar_one_or_none()
    if issue is None:
        raise HTTPException(status_code=404, detail="Not Found")

    return [LabelResponse.from_db(l, BASE, owner, repo) for l in issue.labels]


@router.post(
    "/repos/{owner}/{repo}/issues/{issue_number}/labels",
    status_code=200,
    response_model=list[LabelResponse],
    openapi_extra=LABEL_NAMES_OPENAPI,
)
async def add_issue_labels(
    owner: str,
    repo: str,
    issue_number: int,
    request: Request,
    user: AuthUser,
    db: DbSession,
):
    """Add labels to an issue."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Issue).where(
            Issue.repo_id == repository.id, Issue.number == issue_number
        )
    )
    issue = result.scalar_one_or_none()
    if issue is None:
        raise HTTPException(status_code=404, detail="Not Found")

    existing_names = {label.name for label in (issue.labels or [])}
    added_labels = []
    label_names = await _read_label_names(request)
    labels_by_name: dict[str, Label] = {}
    for lname in dict.fromkeys(label_names):
        if not isinstance(lname, str) or not lname:
            continue
        label = labels_by_name.setdefault(
            lname, await _get_or_create_label(db, repository.id, lname)
        )
        # Check if already assigned
        existing = await db.execute(
            select(IssueLabel).where(
                IssueLabel.issue_id == issue.id,
                IssueLabel.label_id == label.id,
            )
        )
        if existing.scalar_one_or_none() is None:
            db.add(IssueLabel(issue_id=issue.id, label_id=label.id))
            if label.name not in existing_names:
                added_labels.append(label)

    for label in added_labels:
        record_label_event(db, issue, user, "labeled", label)
    await db.commit()
    await db.refresh(issue)
    for label in added_labels:
        await _dispatch_label_event(db, repository, user, issue, "labeled", label)
    if issue.pull_request is not None:
        from app.services.auto_merge_service import process_auto_merge
        await process_auto_merge(db, issue.pull_request, user)
        await db.refresh(issue)
    return [LabelResponse.from_db(l, BASE, owner, repo) for l in issue.labels]


@router.put(
    "/repos/{owner}/{repo}/issues/{issue_number}/labels",
    response_model=list[LabelResponse],
    openapi_extra=LABEL_NAMES_OPENAPI,
)
async def set_issue_labels(
    owner: str,
    repo: str,
    issue_number: int,
    request: Request,
    user: AuthUser,
    db: DbSession,
):
    """Replace all labels on an issue."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Issue).where(
            Issue.repo_id == repository.id, Issue.number == issue_number
        )
    )
    issue = result.scalar_one_or_none()
    if issue is None:
        raise HTTPException(status_code=404, detail="Not Found")

    old_labels = {label.name: label for label in (issue.labels or [])}
    # Remove existing
    await db.execute(
        sa_delete(IssueLabel).where(IssueLabel.issue_id == issue.id)
    )

    label_names = await _read_label_names(request)
    labels_by_name: dict[str, Label] = {}
    for lname in dict.fromkeys(label_names):
        if not isinstance(lname, str) or not lname:
            continue
        label = labels_by_name.setdefault(
            lname, await _get_or_create_label(db, repository.id, lname)
        )
        db.add(IssueLabel(issue_id=issue.id, label_id=label.id))

    requested_labels = {
        label.name: label for label in labels_by_name.values()
    }
    for name, label in requested_labels.items():
        if name not in old_labels:
            record_label_event(db, issue, user, "labeled", label)
    for name, label in old_labels.items():
        if name not in requested_labels:
            record_label_event(db, issue, user, "unlabeled", label)

    await db.commit()
    await db.refresh(issue)
    new_labels = {label.name: label for label in (issue.labels or [])}
    for name, label in new_labels.items():
        if name not in old_labels:
            await _dispatch_label_event(db, repository, user, issue, "labeled", label)
    for name, label in old_labels.items():
        if name not in new_labels:
            await _dispatch_label_event(db, repository, user, issue, "unlabeled", label)
    if issue.pull_request is not None:
        from app.services.auto_merge_service import process_auto_merge
        await process_auto_merge(db, issue.pull_request, user)
        await db.refresh(issue)
    return [LabelResponse.from_db(l, BASE, owner, repo) for l in issue.labels]


@router.delete(
    "/repos/{owner}/{repo}/issues/{issue_number}/labels/{name}",
    status_code=200,
    response_model=list[LabelResponse],
)
async def remove_issue_label(
    owner: str,
    repo: str,
    issue_number: int,
    name: str,
    user: AuthUser,
    db: DbSession,
):
    """Remove a label from an issue."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Issue).where(
            Issue.repo_id == repository.id, Issue.number == issue_number
        )
    )
    issue = result.scalar_one_or_none()
    if issue is None:
        raise HTTPException(status_code=404, detail="Not Found")

    lbl_result = await db.execute(
        select(Label).where(
            Label.repo_id == repository.id, Label.name == name
        )
    )
    label = lbl_result.scalar_one_or_none()
    if label is None:
        raise HTTPException(status_code=404, detail="Label not found")

    assignment = await db.execute(
        select(IssueLabel).where(
            IssueLabel.issue_id == issue.id, IssueLabel.label_id == label.id
        )
    )
    if assignment.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Label not found")

    await db.execute(
        sa_delete(IssueLabel).where(
            IssueLabel.issue_id == issue.id, IssueLabel.label_id == label.id
        )
    )
    record_label_event(db, issue, user, "unlabeled", label)
    await db.commit()
    await db.refresh(issue)
    await _dispatch_label_event(db, repository, user, issue, "unlabeled", label)
    return [LabelResponse.from_db(l, BASE, owner, repo) for l in issue.labels]
