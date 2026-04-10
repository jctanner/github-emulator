"""Check runs and check suites endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import AuthUser, CurrentUser, DbSession, get_repo_or_404
from app.config import settings
from app.models.check import CheckRun, CheckSuite
from app.schemas.user import _fmt_dt, _make_node_id

router = APIRouter(tags=["checks"])

BASE = settings.BASE_URL


def _check_run_json(cr: CheckRun, owner: str, repo_name: str, base_url: str) -> dict:
    api = f"{base_url}/api/v3"
    return {
        "id": cr.id,
        "node_id": _make_node_id("CheckRun", cr.id),
        "head_sha": cr.head_sha,
        "external_id": cr.external_id or "",
        "url": f"{api}/repos/{owner}/{repo_name}/check-runs/{cr.id}",
        "html_url": f"{base_url}/{owner}/{repo_name}/runs/{cr.id}",
        "details_url": cr.details_url,
        "status": cr.status,
        "conclusion": cr.conclusion,
        "started_at": _fmt_dt(cr.started_at),
        "completed_at": _fmt_dt(cr.completed_at),
        "output": {
            "title": cr.output_title,
            "summary": cr.output_summary,
            "text": cr.output_text,
            "annotations_count": 0,
            "annotations_url": f"{api}/repos/{owner}/{repo_name}/check-runs/{cr.id}/annotations",
        },
        "name": cr.name,
        "check_suite": {"id": cr.check_suite_id},
        "app": {"id": 1, "slug": "github-emulator", "name": "GitHub Emulator"},
    }


def _check_suite_json(cs: CheckSuite, owner: str, repo_name: str, base_url: str) -> dict:
    api = f"{base_url}/api/v3"
    return {
        "id": cs.id,
        "node_id": _make_node_id("CheckSuite", cs.id),
        "head_branch": cs.head_branch,
        "head_sha": cs.head_sha,
        "status": cs.status,
        "conclusion": cs.conclusion,
        "url": f"{api}/repos/{owner}/{repo_name}/check-suites/{cs.id}",
        "created_at": _fmt_dt(cs.created_at),
        "updated_at": _fmt_dt(cs.updated_at),
        "app": {"id": 1, "slug": "github-emulator", "name": "GitHub Emulator"},
    }


@router.post("/repos/{owner}/{repo}/check-runs", status_code=201)
async def create_check_run(
    owner: str, repo: str, body: dict, user: AuthUser, db: DbSession
):
    """Create a check run."""
    repository = await get_repo_or_404(owner, repo, db)

    head_sha = body.get("head_sha", "")
    name = body.get("name", "")
    if not head_sha or not name:
        raise HTTPException(status_code=422, detail="head_sha and name are required")

    # Find or create check suite
    result = await db.execute(
        select(CheckSuite).where(
            CheckSuite.repo_id == repository.id,
            CheckSuite.head_sha == head_sha,
        )
    )
    suite = result.scalar_one_or_none()
    if suite is None:
        suite = CheckSuite(
            repo_id=repository.id,
            head_sha=head_sha,
            status="queued",
        )
        db.add(suite)
        await db.flush()

    now = datetime.now(timezone.utc)
    cr = CheckRun(
        check_suite_id=suite.id,
        repo_id=repository.id,
        head_sha=head_sha,
        name=name,
        status=body.get("status", "queued"),
        conclusion=body.get("conclusion"),
        started_at=now if body.get("status") == "in_progress" else None,
        completed_at=now if body.get("conclusion") else None,
        external_id=body.get("external_id"),
        details_url=body.get("details_url"),
    )

    output = body.get("output", {})
    if output:
        cr.output_title = output.get("title")
        cr.output_summary = output.get("summary")
        cr.output_text = output.get("text")

    db.add(cr)
    await db.commit()
    await db.refresh(cr)
    return _check_run_json(cr, owner, repo, BASE)


@router.get("/repos/{owner}/{repo}/check-runs/{check_run_id}")
async def get_check_run(
    owner: str, repo: str, check_run_id: int, db: DbSession, current_user: CurrentUser
):
    """Get a check run."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(CheckRun).where(
            CheckRun.id == check_run_id, CheckRun.repo_id == repository.id
        )
    )
    cr = result.scalar_one_or_none()
    if cr is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _check_run_json(cr, owner, repo, BASE)


@router.patch("/repos/{owner}/{repo}/check-runs/{check_run_id}")
async def update_check_run(
    owner: str, repo: str, check_run_id: int, body: dict, user: AuthUser, db: DbSession
):
    """Update a check run."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(CheckRun).where(
            CheckRun.id == check_run_id, CheckRun.repo_id == repository.id
        )
    )
    cr = result.scalar_one_or_none()
    if cr is None:
        raise HTTPException(status_code=404, detail="Not Found")

    if "status" in body:
        cr.status = body["status"]
    if "conclusion" in body:
        cr.conclusion = body["conclusion"]
        cr.completed_at = datetime.now(timezone.utc)
    if "name" in body:
        cr.name = body["name"]
    if "details_url" in body:
        cr.details_url = body["details_url"]

    output = body.get("output", {})
    if output:
        if "title" in output:
            cr.output_title = output["title"]
        if "summary" in output:
            cr.output_summary = output["summary"]
        if "text" in output:
            cr.output_text = output["text"]

    await db.commit()
    await db.refresh(cr)
    return _check_run_json(cr, owner, repo, BASE)


@router.get("/repos/{owner}/{repo}/commits/{sha}/check-runs")
async def list_check_runs_for_ref(
    owner: str, repo: str, sha: str, db: DbSession, current_user: CurrentUser,
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
):
    """List check runs for a commit SHA."""
    repository = await get_repo_or_404(owner, repo, db)
    query = (
        select(CheckRun)
        .where(CheckRun.repo_id == repository.id, CheckRun.head_sha == sha)
        .order_by(CheckRun.created_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    check_runs = (await db.execute(query)).scalars().all()
    return {
        "total_count": len(check_runs),
        "check_runs": [_check_run_json(cr, owner, repo, BASE) for cr in check_runs],
    }


@router.get("/repos/{owner}/{repo}/check-suites/{check_suite_id}")
async def get_check_suite(
    owner: str, repo: str, check_suite_id: int, db: DbSession, current_user: CurrentUser
):
    """Get a check suite."""
    repository = await get_repo_or_404(owner, repo, db)
    result = await db.execute(
        select(CheckSuite).where(
            CheckSuite.id == check_suite_id, CheckSuite.repo_id == repository.id
        )
    )
    cs = result.scalar_one_or_none()
    if cs is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _check_suite_json(cs, owner, repo, BASE)


@router.post("/repos/{owner}/{repo}/check-suites", status_code=201)
async def create_check_suite(
    owner: str, repo: str, body: dict, user: AuthUser, db: DbSession
):
    """Create a check suite."""
    repository = await get_repo_or_404(owner, repo, db)

    head_sha = body.get("head_sha", "")
    if not head_sha:
        raise HTTPException(status_code=422, detail="head_sha is required")

    cs = CheckSuite(
        repo_id=repository.id,
        head_sha=head_sha,
        head_branch=body.get("head_branch"),
        status="queued",
    )
    db.add(cs)
    await db.commit()
    await db.refresh(cs)
    return _check_suite_json(cs, owner, repo, BASE)


@router.get("/repos/{owner}/{repo}/commits/{sha}/check-suites")
async def list_check_suites_for_ref(
    owner: str, repo: str, sha: str, db: DbSession, current_user: CurrentUser,
):
    """List check suites for a commit SHA."""
    repository = await get_repo_or_404(owner, repo, db)
    query = (
        select(CheckSuite)
        .where(CheckSuite.repo_id == repository.id, CheckSuite.head_sha == sha)
    )
    suites = (await db.execute(query)).scalars().all()
    return {
        "total_count": len(suites),
        "check_suites": [_check_suite_json(cs, owner, repo, BASE) for cs in suites],
    }
