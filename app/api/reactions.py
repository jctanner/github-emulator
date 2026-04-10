"""Reaction endpoints -- list, create, delete reactions on issues and comments."""

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, delete as sa_delete

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.reaction import Reaction
from app.models.issue import Issue
from app.models.comment import IssueComment
from app.schemas.user import SimpleUser, _fmt_dt, _make_node_id

router = APIRouter(tags=["reactions"])

BASE = settings.BASE_URL

VALID_CONTENTS = {"+1", "-1", "laugh", "confused", "heart", "hooray", "rocket", "eyes"}


def _reaction_json(reaction: Reaction, base_url: str) -> dict:
    user_simple = SimpleUser.from_db(reaction.user, base_url).model_dump() if reaction.user else None
    return {
        "id": reaction.id,
        "node_id": _make_node_id("Reaction", reaction.id),
        "user": user_simple,
        "content": reaction.content,
        "created_at": _fmt_dt(reaction.created_at),
    }


# --- Issue reactions ---

@router.get("/repos/{owner}/{repo}/issues/{issue_number}/reactions")
async def list_issue_reactions(
    owner: str, repo: str, issue_number: int, db: DbSession, current_user: CurrentUser,
    content: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List reactions for an issue."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Issue).where(Issue.repo_id == repository.id, Issue.number == issue_number)
    )
    issue = result.scalar_one_or_none()
    if issue is None:
        raise HTTPException(status_code=404, detail="Not Found")

    query = select(Reaction).where(
        Reaction.reactable_type == "issue", Reaction.reactable_id == issue.id
    )
    if content:
        query = query.where(Reaction.content == content)

    query = query.offset((page - 1) * per_page).limit(per_page)
    reactions = (await db.execute(query)).scalars().all()
    return [_reaction_json(r, BASE) for r in reactions]


@router.post("/repos/{owner}/{repo}/issues/{issue_number}/reactions", status_code=201)
async def create_issue_reaction(
    owner: str, repo: str, issue_number: int, body: dict, user: AuthUser, db: DbSession,
):
    """Create a reaction for an issue."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(Issue).where(Issue.repo_id == repository.id, Issue.number == issue_number)
    )
    issue = result.scalar_one_or_none()
    if issue is None:
        raise HTTPException(status_code=404, detail="Not Found")

    content = body.get("content", "")
    if content not in VALID_CONTENTS:
        raise HTTPException(status_code=422, detail=f"Invalid reaction: {content}")

    # Check for existing reaction
    existing = await db.execute(
        select(Reaction).where(
            Reaction.user_id == user.id,
            Reaction.reactable_type == "issue",
            Reaction.reactable_id == issue.id,
            Reaction.content == content,
        )
    )
    reaction = existing.scalar_one_or_none()
    if reaction:
        return _reaction_json(reaction, BASE)

    reaction = Reaction(
        user_id=user.id,
        content=content,
        reactable_type="issue",
        reactable_id=issue.id,
    )
    db.add(reaction)
    await db.commit()
    await db.refresh(reaction)
    return _reaction_json(reaction, BASE)


@router.delete("/repos/{owner}/{repo}/issues/{issue_number}/reactions/{reaction_id}", status_code=204)
async def delete_issue_reaction(
    owner: str, repo: str, issue_number: int, reaction_id: int,
    user: AuthUser, db: DbSession,
):
    """Delete an issue reaction."""
    result = await db.execute(select(Reaction).where(Reaction.id == reaction_id))
    reaction = result.scalar_one_or_none()
    if reaction is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await db.delete(reaction)
    await db.commit()


# --- Issue comment reactions ---

@router.get("/repos/{owner}/{repo}/issues/comments/{comment_id}/reactions")
async def list_comment_reactions(
    owner: str, repo: str, comment_id: int, db: DbSession, current_user: CurrentUser,
    content: str | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List reactions for an issue comment."""
    query = select(Reaction).where(
        Reaction.reactable_type == "issue_comment", Reaction.reactable_id == comment_id
    )
    if content:
        query = query.where(Reaction.content == content)
    query = query.offset((page - 1) * per_page).limit(per_page)
    reactions = (await db.execute(query)).scalars().all()
    return [_reaction_json(r, BASE) for r in reactions]


@router.post("/repos/{owner}/{repo}/issues/comments/{comment_id}/reactions", status_code=201)
async def create_comment_reaction(
    owner: str, repo: str, comment_id: int, body: dict, user: AuthUser, db: DbSession,
):
    """Create a reaction for an issue comment."""
    content = body.get("content", "")
    if content not in VALID_CONTENTS:
        raise HTTPException(status_code=422, detail=f"Invalid reaction: {content}")

    existing = await db.execute(
        select(Reaction).where(
            Reaction.user_id == user.id,
            Reaction.reactable_type == "issue_comment",
            Reaction.reactable_id == comment_id,
            Reaction.content == content,
        )
    )
    reaction = existing.scalar_one_or_none()
    if reaction:
        return _reaction_json(reaction, BASE)

    reaction = Reaction(
        user_id=user.id,
        content=content,
        reactable_type="issue_comment",
        reactable_id=comment_id,
    )
    db.add(reaction)
    await db.commit()
    await db.refresh(reaction)
    return _reaction_json(reaction, BASE)


@router.delete("/repos/{owner}/{repo}/issues/comments/{comment_id}/reactions/{reaction_id}", status_code=204)
async def delete_comment_reaction(
    owner: str, repo: str, comment_id: int, reaction_id: int,
    user: AuthUser, db: DbSession,
):
    """Delete a comment reaction."""
    result = await db.execute(select(Reaction).where(Reaction.id == reaction_id))
    reaction = result.scalar_one_or_none()
    if reaction is None:
        raise HTTPException(status_code=404, detail="Not Found")
    await db.delete(reaction)
    await db.commit()
