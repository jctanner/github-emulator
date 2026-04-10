"""Label endpoints -- repo labels and issue labels."""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, delete as sa_delete

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.label import Label
from app.models.issue import Issue, IssueLabel
from app.schemas.label import LabelCreate, LabelResponse, LabelUpdate

router = APIRouter(tags=["labels"])

BASE = settings.BASE_URL


# ---------------------------------------------------------------------------
# Repo-level label CRUD
# ---------------------------------------------------------------------------

@router.get("/repos/{owner}/{repo}/labels")
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


@router.post("/repos/{owner}/{repo}/labels", status_code=201)
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


@router.get("/repos/{owner}/{repo}/labels/{name}")
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


@router.patch("/repos/{owner}/{repo}/labels/{name}")
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

@router.get("/repos/{owner}/{repo}/issues/{issue_number}/labels")
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


@router.post("/repos/{owner}/{repo}/issues/{issue_number}/labels", status_code=200)
async def add_issue_labels(
    owner: str,
    repo: str,
    issue_number: int,
    body: dict,
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

    label_names = body.get("labels", [])
    for lname in label_names:
        lbl_result = await db.execute(
            select(Label).where(
                Label.repo_id == repository.id, Label.name == lname
            )
        )
        label = lbl_result.scalar_one_or_none()
        if label:
            # Check if already assigned
            existing = await db.execute(
                select(IssueLabel).where(
                    IssueLabel.issue_id == issue.id,
                    IssueLabel.label_id == label.id,
                )
            )
            if existing.scalar_one_or_none() is None:
                db.add(IssueLabel(issue_id=issue.id, label_id=label.id))

    await db.commit()
    await db.refresh(issue)
    return [LabelResponse.from_db(l, BASE, owner, repo) for l in issue.labels]


@router.put("/repos/{owner}/{repo}/issues/{issue_number}/labels")
async def set_issue_labels(
    owner: str,
    repo: str,
    issue_number: int,
    body: dict,
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

    # Remove existing
    await db.execute(
        sa_delete(IssueLabel).where(IssueLabel.issue_id == issue.id)
    )

    label_names = body.get("labels", [])
    for lname in label_names:
        lbl_result = await db.execute(
            select(Label).where(
                Label.repo_id == repository.id, Label.name == lname
            )
        )
        label = lbl_result.scalar_one_or_none()
        if label:
            db.add(IssueLabel(issue_id=issue.id, label_id=label.id))

    await db.commit()
    await db.refresh(issue)
    return [LabelResponse.from_db(l, BASE, owner, repo) for l in issue.labels]


@router.delete("/repos/{owner}/{repo}/issues/{issue_number}/labels/{name}", status_code=200)
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

    await db.execute(
        sa_delete(IssueLabel).where(
            IssueLabel.issue_id == issue.id, IssueLabel.label_id == label.id
        )
    )
    await db.commit()
    await db.refresh(issue)
    return [LabelResponse.from_db(l, BASE, owner, repo) for l in issue.labels]
