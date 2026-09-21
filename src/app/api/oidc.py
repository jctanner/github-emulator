"""GitHub Actions OIDC issuer endpoints for local integration tests."""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.api.deps import DbSession
from app.models.actions import Workflow
from app.models.repository import Repository
from app.models.user import User
from app.services.oidc_service import issue_for_job, issuer, jwks, subject_for
from app.services.job_token_service import validate_job_token

router = APIRouter(tags=["oidc"])


@router.get("/.well-known/openid-configuration")
async def configuration(request: Request):
    base = issuer()
    return {
        "issuer": base,
        "jwks_uri": f"{base}/.well-known/jwks.json",
        "id_token_signing_alg_values_supported": ["RS256"],
    }


@router.get("/.well-known/jwks.json")
async def keys():
    return jwks()


@router.get("/actions/oidc/token")
async def actions_token(request: Request, db: DbSession, audience: str | None = None):
    """Issue an OIDC token for the job whose request token is presented.

    The caller chooses only the audience, as on GitHub. Everything that
    identifies the run - repository, workflow, ref, event, actor - is derived
    from the job the request token belongs to. Letting a caller name its own
    subject would let any workflow request a token for any repository, which
    makes claim validation downstream meaningless.
    """
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Actions OIDC request token required")

    validated = await validate_job_token(db, token)
    if validated is None:
        raise HTTPException(status_code=401, detail="invalid Actions OIDC request token")
    job, run = validated

    # GitHub refuses the request unless the job asked for id-token: write. A
    # job with no permissions block at all is treated as permissive, the same
    # deviation the job-token scope check documents.
    if job.permissions is not None and job.permissions.get("id-token") != "write":
        raise HTTPException(
            status_code=403,
            detail=(
                "Resource not accessible by integration: the job's permissions "
                "do not include id-token: write"
            ),
        )

    repository = (
        await db.execute(select(Repository).where(Repository.id == run.repo_id))
    ).scalar_one_or_none()
    workflow = (
        await db.execute(select(Workflow).where(Workflow.id == run.workflow_id))
    ).scalar_one_or_none()
    actor = (
        await db.execute(select(User).where(User.id == run.actor_id))
    ).scalar_one_or_none()

    full_name = repository.full_name if repository else ""
    ref = (run.trigger_payload or {}).get("ref") or f"refs/heads/{run.head_branch}"
    workflow_path = workflow.path if workflow else ""
    workflow_ref = f"{full_name}/{workflow_path}@{ref}" if workflow_path else ""

    claims = {
        "sub": subject_for(full_name, run.event, ref),
        "repository": full_name,
        "repository_owner": full_name.split("/", 1)[0] if full_name else "",
        "repository_id": str(repository.id) if repository else "",
        "run_id": str(run.id),
        "run_number": str(run.run_number),
        "run_attempt": str(run.run_attempt),
        "workflow": workflow.name if workflow else "",
        "workflow_ref": workflow_ref,
        # The job's own workflow, which for an inlined reusable workflow is
        # the workflow that actually defined the job.
        "job_workflow_ref": workflow_ref,
        "job_workflow_sha": run.head_sha,
        "ref": ref,
        "sha": run.head_sha,
        "event_name": run.event,
        "actor": actor.login if actor else "",
        "actor_id": str(actor.id) if actor else "",
        "runner_environment": "self-hosted",
    }
    # GitHub's default audience is the repository owner's URL. Callers that
    # care, such as Fullsend's mint action, always pass one explicitly.
    resolved_audience = audience or f"{issuer()}/{claims['repository_owner']}"
    return {"value": issue_for_job(claims, resolved_audience), "count": 1}
