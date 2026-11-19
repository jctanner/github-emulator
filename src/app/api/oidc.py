"""GitHub Actions OIDC issuer endpoints for local integration tests."""

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.api.deps import DbSession
from app.models.actions import Workflow
from app.models.branch import Branch
from app.models.repository import Repository
from app.models.user import User
from app.services.job_token_service import validate_job_token
from app.services.oidc_service import issue_for_job, issuer, jwks, subject_for

router = APIRouter(tags=["oidc"])


@router.get("/.well-known/openid-configuration")
async def configuration(request: Request):
    base = issuer()
    # The fields GitHub's own discovery document carries; jwks_uri is the
    # path GitHub advertises (/.well-known/jwks.json is still served).
    return {
        "issuer": base,
        "jwks_uri": f"{base}/.well-known/jwks",
        "subject_types_supported": ["public", "pairwise"],
        "response_types_supported": ["id_token"],
        "claims_supported": CLAIMS_SUPPORTED,
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid"],
    }


# The claims a token from this issuer carries.
CLAIMS_SUPPORTED = [
    "sub", "aud", "exp", "iat", "iss", "jti", "nbf", "ref", "sha", "repository", "repository_id",
    "repository_owner", "repository_owner_id", "run_id", "run_number", "run_attempt", "actor",
    "actor_id", "workflow", "workflow_ref", "head_ref", "base_ref", "event_name", "ref_type",
    "ref_protected", "repository_visibility", "check_run_id", "job_workflow_ref", "job_workflow_sha",
    "runner_environment",
]


@router.get("/.well-known/jwks.json")
@router.get("/.well-known/jwks")
async def keys():
    # GitHub's Actions issuer serves its keys at /.well-known/jwks; relying
    # parties configured for GitHub use that path directly.
    return jwks()


def _visibility(repository: Repository | None) -> str:
    if repository is None:
        return ""
    if repository.visibility == "internal":
        return "internal"
    return "private" if repository.private else "public"


def _owner_id(repository: Repository | None) -> str:
    if repository is None:
        return ""
    if repository.owner_type == "Organization" and repository.organization is not None:
        return str(repository.organization.id)
    return str(repository.owner_id)


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
    pull_request = (run.trigger_payload or {}).get("pull_request") or {}
    ref_protected = False
    if repository is not None and ref.startswith("refs/heads/"):
        branch = (
            await db.execute(
                select(Branch).where(Branch.repo_id == repository.id, Branch.name == ref[len("refs/heads/"):])
            )
        ).scalar_one_or_none()
        ref_protected = bool(branch and branch.protected)

    claims = {
        "sub": subject_for(full_name, run.event, ref),
        "repository": full_name,
        "repository_owner": full_name.split("/", 1)[0] if full_name else "",
        "repository_id": str(repository.id) if repository else "",
        # GitHub's id for the owner, the same as the repository's owner.id
        # in the REST API: the organization's id for an org-owned repository.
        # A relying party pins an owner by id because names can be re-used.
        # Users and organizations have separate id sequences here, unlike on
        # GitHub, so a user and an organization can share an id.
        "repository_owner_id": _owner_id(repository),
        "run_id": str(run.id),
        "run_number": str(run.run_number),
        "run_attempt": str(run.run_attempt),
        "workflow": workflow.name if workflow else "",
        "workflow_ref": workflow_ref,
        # The job's own workflow, which for an inlined reusable workflow is
        # the workflow that actually defined the job - recorded on the job at
        # materialization. It differs from workflow_ref exactly when the run's
        # workflow called another one, which is what a mint keys trust on.
        "job_workflow_ref": job.workflow_ref or workflow_ref,
        "job_workflow_sha": run.head_sha,
        "ref": ref,
        "ref_type": "tag" if ref.startswith("refs/tags/") else "branch",
        # Strings, as GitHub writes them.
        "ref_protected": "true" if ref_protected else "false",
        "head_ref": str((pull_request.get("head") or {}).get("ref") or ""),
        "base_ref": str((pull_request.get("base") or {}).get("ref") or ""),
        "repository_visibility": _visibility(repository),
        # The job's id. On GitHub a job's check run shares it; the emulator
        # creates no check run for a job.
        "check_run_id": str(job.id),
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
