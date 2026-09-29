"""Contracts for the API-client site-administration surface."""

import pytest
from sqlalchemy import event, select

from app.models.import_job import ImportJob
from app.models.issue import Issue
from app.models.organization import Organization
from app.models.pull_request import PullRequest
from app.models.user import User

from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_admin_summary_and_inventory(client, admin_token):
    headers = auth_headers(admin_token)
    summary = await client.get("/admin/api/summary", headers=headers)
    users = await client.get("/admin/api/users", headers=headers)
    repositories = await client.get("/admin/api/repositories", headers=headers)

    assert summary.status_code == 200
    assert summary.json()["users"] >= 1
    assert users.status_code == 200
    assert repositories.status_code == 200


@pytest.mark.asyncio
async def test_admin_user_and_organization_lifecycle(client, admin_token):
    headers = auth_headers(admin_token)
    user = await client.post(
        "/admin/api/users",
        json={"login": "frontend-admin-test", "password": "secret"},
        headers=headers,
    )
    organization = await client.post(
        "/admin/api/organizations",
        json={"login": "frontend-admin-org"},
        headers=headers,
    )

    assert user.status_code == 201
    assert organization.status_code == 201
    assert (
        await client.delete(
            f"/admin/api/organizations/{organization.json()['id']}", headers=headers
        )
    ).status_code == 204
    assert (
        await client.delete(
            f"/admin/api/users/{user.json()['id']}", headers=headers
        )
    ).status_code == 204


@pytest.mark.asyncio
async def test_admin_import_defaults_to_acting_admin(client, admin_token, admin_user):
    response = await client.post(
        "/admin/api/imports",
        json={"source_url": "https://github.com/octocat/hello-world"},
        headers=auth_headers(admin_token),
    )
    assert response.status_code == 201
    assert response.json()["owner"] == admin_user.login


@pytest.mark.asyncio
async def test_admin_import_targets_existing_user(
    client, admin_token, test_user, db_session
):
    response = await client.post(
        "/admin/api/imports",
        json={
            "source_url": "https://github.com/octocat/hello-world",
            "owner_login": test_user.login,
        },
        headers=auth_headers(admin_token),
    )
    assert response.status_code == 201
    assert response.json()["owner"] == test_user.login
    job = (
        await db_session.execute(
            select(ImportJob).where(ImportJob.id == response.json()["id"])
        )
    ).scalar_one()
    assert job.owner_id == test_user.id
    assert job.owner_type == "User"


@pytest.mark.asyncio
async def test_admin_import_targets_existing_organization(
    client, admin_token, admin_user, db_session
):
    org = Organization(login="acme-corp")
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)

    response = await client.post(
        "/admin/api/imports",
        json={
            "source_url": "https://github.com/octocat/hello-world",
            "owner_login": "acme-corp",
        },
        headers=auth_headers(admin_token),
    )
    assert response.status_code == 201
    assert response.json()["owner"] == "acme-corp"
    job = (
        await db_session.execute(
            select(ImportJob).where(ImportJob.id == response.json()["id"])
        )
    ).scalar_one()
    assert job.owner_id == admin_user.id
    assert job.owner_type == "Organization"
    assert job.org_login == "acme-corp"


@pytest.mark.asyncio
async def test_admin_import_unknown_destination_requires_create_as(
    client, admin_token
):
    response = await client.post(
        "/admin/api/imports",
        json={
            "source_url": "https://github.com/octocat/hello-world",
            "owner_login": "does-not-exist-yet",
        },
        headers=auth_headers(admin_token),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_admin_import_auto_creates_new_user(client, admin_token, db_session):
    response = await client.post(
        "/admin/api/imports",
        json={
            "source_url": "https://github.com/octocat/hello-world",
            "owner_login": "brand-new-user",
            "create_as": "User",
        },
        headers=auth_headers(admin_token),
    )
    assert response.status_code == 201
    assert response.json()["owner"] == "brand-new-user"
    created = (
        await db_session.execute(
            select(User).where(User.login == "brand-new-user")
        )
    ).scalar_one()
    assert created.type == "User"


@pytest.mark.asyncio
async def test_admin_import_auto_creates_new_organization(
    client, admin_token, db_session
):
    response = await client.post(
        "/admin/api/imports",
        json={
            "source_url": "https://github.com/octocat/hello-world",
            "owner_login": "brand-new-org",
            "create_as": "Organization",
        },
        headers=auth_headers(admin_token),
    )
    assert response.status_code == 201
    assert response.json()["owner"] == "brand-new-org"
    created = (
        await db_session.execute(
            select(Organization).where(Organization.login == "brand-new-org")
        )
    ).scalar_one()
    assert created.login == "brand-new-org"


@pytest.mark.asyncio
async def test_repository_installations_list_is_typed(
    client, test_token, test_repo_with_init
):
    owner, repo, _ = test_repo_with_init
    response = await client.get(
        f"/api/v3/repos/{owner}/{repo}/installations",
        headers=auth_headers(test_token),
    )
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_admin_issues_uses_one_projection_query(
    client,
    admin_token,
    db_session,
    db_engine,
    test_user,
    test_repo_with_init,
):
    """Issue inventory does not load ORM graphs or query repositories per row."""
    _, _, repository = test_repo_with_init
    issue = Issue(
        repo_id=repository["id"],
        number=2,
        user_id=test_user.id,
        title="Admin inventory pull request",
    )
    db_session.add(issue)
    await db_session.flush()
    db_session.add(
        PullRequest(
            issue_id=issue.id,
            repo_id=repository["id"],
            head_ref="feature",
            head_sha="1" * 40,
            base_ref="main",
            base_sha="0" * 40,
        )
    )
    await db_session.commit()

    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(" ".join(statement.split()).lower())

    event.listen(db_engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        response = await client.get(
            "/admin/api/issues", headers=auth_headers(admin_token)
        )
    finally:
        event.remove(db_engine.sync_engine, "before_cursor_execute", record_statement)

    assert response.status_code == 200
    row = next(item for item in response.json() if item["id"] == issue.id)
    assert row["repository"] == "testuser/init-repo"
    assert row["is_pull_request"] is True
    inventory_queries = [
        statement
        for statement in statements
        if " from issues " in f" {statement} "
    ]
    assert len(inventory_queries) == 1, statements
    assert "join repositories" in inventory_queries[0]
    assert "join pull_requests" in inventory_queries[0]


@pytest.mark.asyncio
async def test_admin_runners_name_their_scope(client, admin_token, db_session):
    """A runner's scope names the repository or org, not its foreign key.

    The admin runners page existed to answer "what is this runner attached
    to", and answered "repository:418" — the one form of the answer an
    operator cannot resolve without a database. Repository and organization
    runners now carry the full name, and a link to it.
    """
    from app.models.actions import Runner
    from app.models.repository import Repository

    headers = auth_headers(admin_token)
    created = await client.post(
        "/api/v3/user/repos", json={"name": "runner-scope"}, headers=headers
    )
    assert created.status_code in (200, 201), created.text
    repo_full = created.json()["full_name"]
    repo_id = created.json()["id"]

    org = Organization(login="runner-org", name="Runner Org")
    db_session.add(org)
    await db_session.flush()

    db_session.add_all([
        Runner(name="repo-runner", os="linux", status="online", repo_id=repo_id,
               labels=["self-hosted", "fullsend"]),
        Runner(name="org-runner", os="linux", status="online", org_id=org.id, labels=[]),
        Runner(name="site-runner", os="linux", status="online", labels=[]),
        Runner(name="ghost-runner", os="linux", status="online", repo_id=999999, labels=[]),
    ])
    await db_session.commit()

    listed = await client.get("/admin/api/runners", headers=headers)
    assert listed.status_code == 200, listed.text
    by_name = {r["name"]: r for r in listed.json()}

    repo_runner = by_name["repo-runner"]
    assert repo_runner["scope"] == repo_full
    assert repo_runner["scope_kind"] == "repository"
    assert repo_runner["scope_url"] == f"/ui/{repo_full}"
    # Labels decide which jobs a runner accepts; the page shows them, so the
    # payload has to carry them.
    assert repo_runner["labels"] == ["self-hosted", "fullsend"]

    org_runner = by_name["org-runner"]
    assert org_runner["scope"] == "runner-org"
    assert org_runner["scope_kind"] == "organization"
    assert org_runner["scope_url"] == "/ui/orgs/runner-org"

    site_runner = by_name["site-runner"]
    assert site_runner["scope_kind"] == "site"
    assert site_runner["scope"] == "All repositories"
    assert site_runner["scope_url"] is None

    # A key pointing at something deleted says so. Rendering a bare number is
    # what this change removes; rendering nothing would read as "not attached",
    # which is worse than the number was.
    ghost = by_name["ghost-runner"]
    assert ghost["scope_kind"] == "repository"
    assert "999999" in ghost["scope"] and "deleted" in ghost["scope"]
    assert ghost["scope_url"] is None


@pytest.mark.asyncio
async def test_admin_can_remove_a_site_scoped_runner(client, admin_token, db_session):
    """A site-scoped runner had no delete route at any scope.

    The Actions API deletes repository and enterprise runners; a site runner
    carries none of those keys, so a shim replaced a month ago stayed listed
    with nothing able to remove it. Deletion detaches the jobs that ran on it
    instead of orphaning rows at an unenforced foreign key.
    """
    from app.models.actions import Runner, WorkflowJob

    headers = auth_headers(admin_token)
    runner = Runner(name="retired-shim", os="linux", status="offline", labels=[])
    db_session.add(runner)
    await db_session.flush()
    job = WorkflowJob(
        run_id=1, name="ran-here", status="completed", conclusion="success",
        runner_id=runner.id, runner_name="retired-shim",
    )
    db_session.add(job)
    await db_session.commit()
    runner_id, job_id = runner.id, job.id

    removed = await client.delete(f"/admin/api/runners/{runner_id}", headers=headers)
    assert removed.status_code == 204, removed.text

    listed = await client.get("/admin/api/runners", headers=headers)
    assert "retired-shim" not in {r["name"] for r in listed.json()}

    # The job survives and still says which runner ran it; only the pointer
    # to the removed row is gone.
    kept = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.id == job_id)
    )).scalar_one()
    await db_session.refresh(kept)
    assert kept.runner_name == "retired-shim"
    assert kept.runner_id is None

    assert (await client.delete(
        f"/admin/api/runners/{runner_id}", headers=headers
    )).status_code == 404


@pytest.mark.asyncio
async def test_admin_actions_lists_unfinished_runs_across_repos(
    client, admin_token, db_session
):
    """Every in-flight run in one place, newest activity first.

    Actions state was only readable one repository at a time, which is the
    wrong shape for "what is running, and what is stuck": a run queued behind
    a label no runner registers looks exactly like one about to start until
    they are seen together.
    """
    from datetime import datetime, timedelta

    from app.models.actions import Workflow, WorkflowJob, WorkflowRun

    headers = auth_headers(admin_token)

    # Every HTTP call first: the ORM work below holds a transaction open, and
    # interleaving the two deadlocks SQLite ("Database is busy").
    repos = {}
    for name in ("actions-a", "actions-b"):
        created = await client.post(
            "/api/v3/user/repos", json={"name": name}, headers=headers
        )
        assert created.status_code in (200, 201), created.text
        repos[name] = (created.json()["id"], created.json()["full_name"])

    actor_id = (await db_session.execute(select(User))).scalars().first().id
    base = datetime(2026, 9, 25, 12, 0, 0)
    made = []
    workflow_ids = []
    for index, name in enumerate(("actions-a", "actions-b")):
        repo_id, full_name = repos[name]
        workflow = Workflow(repo_id=repo_id, name=f"wf-{name}", path=".github/x.yml")
        db_session.add(workflow)
        await db_session.flush()
        run = WorkflowRun(
            workflow_id=workflow.id, repo_id=repo_id, head_sha="a" * 40,
            head_branch="main", event="push", status="queued",
            run_number=1, run_attempt=1, actor_id=actor_id,
            created_at=base, updated_at=base + timedelta(minutes=index),
        )
        db_session.add(run)
        await db_session.flush()
        made.append((full_name, run.id))
        workflow_ids.append(workflow.id)
        db_session.add_all([
            WorkflowJob(run_id=run.id, name="done", status="completed",
                        conclusion="success"),
            WorkflowJob(run_id=run.id, name="stuck", status="queued",
                        labels=["self-hosted", "nonexistent-label"]),
        ])

    # A finished run must not appear, however recently it was touched.
    finished = WorkflowRun(
        workflow_id=workflow_ids[0], repo_id=repos["actions-a"][0], head_sha="b" * 40,
        head_branch="main", event="push", status="completed",
        conclusion="success", run_number=99, run_attempt=1, actor_id=actor_id,
        created_at=base, updated_at=base + timedelta(hours=1),
    )
    db_session.add(finished)
    await db_session.commit()

    listed = await client.get("/admin/api/actions", headers=headers)
    assert listed.status_code == 200, listed.text
    page = listed.json()
    assert page["page"] == 1 and page["per_page"] == 50
    body = page["items"]
    ids = [row["id"] for row in body]

    assert made[0][1] in ids and made[1][1] in ids
    assert 99 not in [row["run_number"] for row in body], "a completed run was listed"

    # Most recent activity first: actions-b was updated a minute later.
    assert ids.index(made[1][1]) < ids.index(made[0][1])

    row = next(r for r in body if r["id"] == made[1][1])
    assert row["repository"] == made[1][0]
    assert row["status"] == "queued"
    assert row["jobs_total"] == 2 and row["jobs_completed"] == 1
    # Only the unfinished job is listed, with the labels it is waiting on --
    # which is what explains a run that never starts.
    assert [j["name"] for j in row["active_jobs"]] == ["stuck"]
    assert row["active_jobs"][0]["labels"] == ["self-hosted", "nonexistent-label"]
    assert row["url"] == f"/ui/{made[1][0]}/actions/runs/{made[1][1]}"


@pytest.mark.asyncio
async def test_admin_actions_keeps_runs_whose_repository_is_gone(
    client, admin_token, db_session
):
    """A deleted repository must not hide its still-queued runs.

    The first version of this endpoint inner-joined Repository, which dropped
    every run whose repository had been removed. On the development stack that
    was 43 of 73 in-flight runs: the page reported 30 and looked complete.
    Deleting a repository does not delete its runs, so those are precisely the
    rows nobody knows about.
    """
    from datetime import datetime

    from app.models.actions import Workflow, WorkflowJob, WorkflowRun
    from app.models.repository import Repository

    headers = auth_headers(admin_token)
    created = await client.post(
        "/api/v3/user/repos", json={"name": "doomed"}, headers=headers
    )
    assert created.status_code in (200, 201), created.text
    repo_id = created.json()["id"]

    actor_id = (await db_session.execute(select(User))).scalars().first().id
    workflow = Workflow(repo_id=repo_id, name="wf", path=".github/x.yml")
    db_session.add(workflow)
    await db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id, repo_id=repo_id, head_sha="c" * 40,
        head_branch="main", event="push", status="queued", run_number=1,
        run_attempt=1, actor_id=actor_id,
        created_at=datetime(2026, 9, 25, 12, 0, 0),
        updated_at=datetime(2026, 9, 25, 12, 0, 0),
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(WorkflowJob(run_id=run.id, name="orphaned", status="queued"))
    await db_session.commit()
    run_id = run.id

    # Remove the repository row the way a deletion would, leaving the run.
    await db_session.execute(
        Repository.__table__.delete().where(Repository.id == repo_id)
    )
    await db_session.commit()

    listed = await client.get("/admin/api/actions", headers=headers)
    assert listed.status_code == 200, listed.text
    row = next((r for r in listed.json()["items"] if r["id"] == run_id), None)
    assert row is not None, "a run whose repository was deleted vanished from the list"
    assert str(repo_id) in row["repository"] and "deleted" in row["repository"]
    # No link, because there is nowhere to go.
    assert row["url"] is None
    assert [j["name"] for j in row["active_jobs"]] == ["orphaned"]


@pytest.mark.asyncio
async def test_admin_can_cancel_a_run_whose_repository_is_gone(
    client, admin_token, db_session
):
    """The stale backlog is mostly runs the Actions cancel route cannot reach.

    That route is addressed by owner and repository; deleting a repository
    leaves its runs behind, so the runs most in need of cancelling are exactly
    the ones it cannot name. Cancelling by run id reaches them, and keeps the
    history: the run and its jobs stay, marked cancelled.
    """
    from datetime import datetime

    from app.models.actions import Workflow, WorkflowJob, WorkflowRun
    from app.models.repository import Repository

    headers = auth_headers(admin_token)
    created = await client.post(
        "/api/v3/user/repos", json={"name": "cancel-me"}, headers=headers
    )
    assert created.status_code in (200, 201), created.text
    repo_id = created.json()["id"]

    actor_id = (await db_session.execute(select(User))).scalars().first().id
    workflow = Workflow(repo_id=repo_id, name="wf", path=".github/x.yml")
    db_session.add(workflow)
    await db_session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id, repo_id=repo_id, head_sha="d" * 40,
        head_branch="main", event="push", status="queued", run_number=1,
        run_attempt=1, actor_id=actor_id,
        created_at=datetime(2026, 9, 25, 12, 0, 0),
        updated_at=datetime(2026, 9, 25, 12, 0, 0),
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(WorkflowJob(run_id=run.id, name="stranded", status="queued"))
    await db_session.commit()
    run_id = run.id

    await db_session.execute(
        Repository.__table__.delete().where(Repository.id == repo_id)
    )
    await db_session.commit()

    cancelled = await client.post(
        f"/admin/api/actions/{run_id}/cancel", headers=headers
    )
    assert cancelled.status_code == 204, cancelled.text

    listed = await client.get("/admin/api/actions", headers=headers)
    assert run_id not in [r["id"] for r in listed.json()["items"]], "still listed as in flight"

    # The record survives, marked cancelled rather than removed.
    after = (await db_session.execute(
        select(WorkflowRun).where(WorkflowRun.id == run_id)
    )).scalar_one()
    await db_session.refresh(after)
    assert after.status == "completed" and after.conclusion == "cancelled"
    job = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == run_id)
    )).scalars().one()
    await db_session.refresh(job)
    assert job.status == "completed" and job.conclusion == "cancelled"

    assert (await client.post(
        "/admin/api/actions/999999/cancel", headers=headers
    )).status_code == 404


async def test_admin_can_delete_an_organization_someone_joined(client, admin_token, db_session):
    """Deleting an organization must take its memberships and teams with it.

    An organization created through the public route records its creator as
    a member. The admin delete removed only the organization row; SQLAlchemy
    then nulled the membership's org_id, which is NOT NULL, and every such
    delete answered 500 - seen live on 2026-09-27 cleaning up a throwaway
    organization. An organization that still owns repositories is refused
    with 409 rather than orphaning them.
    """
    from sqlalchemy import select
    from app.models.organization import OrgMembership

    headers = auth_headers(admin_token)
    created = await client.post(
        "/api/v3/orgs", json={"login": "joined-org", "name": "joined-org"}, headers=headers
    )
    assert created.status_code == 201, created.text
    org_id = created.json()["id"]
    assert (await db_session.execute(
        select(OrgMembership).where(OrgMembership.org_id == org_id)
    )).scalars().first() is not None, "the creator should be a member"

    resp = await client.delete(f"/admin/api/organizations/{org_id}", headers=headers)
    assert resp.status_code == 204, resp.text
    db_session.expire_all()
    assert (await db_session.execute(
        select(OrgMembership).where(OrgMembership.org_id == org_id)
    )).scalars().first() is None
    assert (await client.get("/api/v3/orgs/joined-org", headers=headers)).status_code == 404


async def test_admin_cannot_delete_an_organization_that_owns_repositories(client, admin_token):
    headers = auth_headers(admin_token)
    created = await client.post(
        "/api/v3/orgs", json={"login": "busy-org", "name": "busy-org"}, headers=headers
    )
    assert created.status_code == 201
    repo = await client.post(
        "/api/v3/orgs/busy-org/repos", json={"name": "kept"}, headers=headers
    )
    assert repo.status_code == 201, repo.text
    resp = await client.delete(f"/admin/api/organizations/{created.json()['id']}", headers=headers)
    assert resp.status_code == 409


# --- the job listing and its settlements ----------------------------------------

async def _seed_runs_with_jobs(client, admin_token, db_session, count=3):
    """A repository with `count` runs, each holding one finished and one
    queued job, plus one in-progress job on a named runner in the last run."""
    from datetime import datetime, timedelta
    from app.models.actions import Workflow, WorkflowJob, WorkflowRun
    headers = auth_headers(admin_token)
    created = await client.post("/api/v3/user/repos", json={"name": "jobs-repo"}, headers=headers)
    assert created.status_code in (200, 201), created.text
    repo_id, full_name = created.json()["id"], created.json()["full_name"]
    actor_id = (await db_session.execute(select(User))).scalars().first().id
    workflow = Workflow(repo_id=repo_id, name="wf", path=".github/wf.yml")
    db_session.add(workflow)
    await db_session.flush()
    base = datetime(2026, 9, 28, 12, 0, 0)
    runs = []
    for index in range(count):
        run = WorkflowRun(
            workflow_id=workflow.id, repo_id=repo_id, head_sha="a" * 40, head_branch="main",
            event="push", status="in_progress", run_number=index + 1, run_attempt=1,
            actor_id=actor_id, created_at=base + timedelta(minutes=index),
            updated_at=base + timedelta(minutes=index),
        )
        db_session.add(run)
        await db_session.flush()
        db_session.add(WorkflowJob(run_id=run.id, name="done", status="completed", conclusion="success",
                                   steps=[{"number": 1, "name": "a", "status": "completed", "conclusion": "success"}]))
        db_session.add(WorkflowJob(run_id=run.id, name="waiting", status="queued", labels=["fullsend"]))
        runs.append(run)
    db_session.add(WorkflowJob(
        run_id=runs[-1].id, name="running", status="in_progress", runner_name="fullsend-agent-runner",
        started_at=base, labels=["fullsend"],
        steps=[{"number": 1, "name": "checkout", "status": "completed", "conclusion": "success"},
               {"number": 2, "name": "agent", "status": "in_progress", "conclusion": None}],
    ))
    await db_session.commit()
    return full_name, runs


@pytest.mark.asyncio
async def test_admin_jobs_lists_unfinished_jobs_by_default_and_pages_all(client, admin_token, db_session):
    full_name, runs = await _seed_runs_with_jobs(client, admin_token, db_session)
    headers = auth_headers(admin_token)
    listed = await client.get("/admin/api/jobs", headers=headers)
    assert listed.status_code == 200, listed.text
    page = listed.json()
    names = [item["name"] for item in page["items"]]
    assert "done" not in names, "a finished job was listed by default"
    assert names.count("waiting") == 3 and names.count("running") == 1
    assert page["total_count"] == 4
    running = next(item for item in page["items"] if item["name"] == "running")
    assert running["repository"] == full_name and running["runner_name"] == "fullsend-agent-runner"
    assert running["current_step"]["name"] == "agent" and running["current_step"]["status"] == "in_progress"
    assert running["url"] == f"/ui/{full_name}/actions/jobs/{running['id']}"

    everything = (await client.get("/admin/api/jobs", params={"scope": "all", "per_page": 3, "page": 1}, headers=headers)).json()
    assert everything["total_count"] == 7 and len(everything["items"]) == 3 and everything["page"] == 1
    second = (await client.get("/admin/api/jobs", params={"scope": "all", "per_page": 3, "page": 3}, headers=headers)).json()
    assert len(second["items"]) == 1
    # Newest first, and the pages tile the whole listing without overlap.
    whole = [i["id"] for i in (await client.get("/admin/api/jobs", params={"scope": "all", "per_page": 200}, headers=headers)).json()["items"]]
    assert whole == sorted(whole, reverse=True) and len(whole) == 7
    assert [i["id"] for i in everything["items"]] == whole[:3]
    assert [i["id"] for i in second["items"]] == whole[6:]


@pytest.mark.asyncio
async def test_admin_jobs_filters_by_status_runner_label_and_repository(client, admin_token, db_session):
    full_name, _runs = await _seed_runs_with_jobs(client, admin_token, db_session)
    headers = auth_headers(admin_token)
    by_status = (await client.get("/admin/api/jobs", params={"status": "completed"}, headers=headers)).json()
    assert {i["name"] for i in by_status["items"]} == {"done"} and by_status["total_count"] == 3
    by_runner = (await client.get("/admin/api/jobs", params={"scope": "all", "runner": "fullsend-agent-runner"}, headers=headers)).json()
    assert [i["name"] for i in by_runner["items"]] == ["running"]
    by_label = (await client.get("/admin/api/jobs", params={"label": "fullsend"}, headers=headers)).json()
    assert by_label["total_count"] == 4 and all("fullsend" in i["labels"] for i in by_label["items"])
    by_repo = (await client.get("/admin/api/jobs", params={"repository": "nobody/nowhere"}, headers=headers)).json()
    assert by_repo["total_count"] == 0 and by_repo["items"] == []
    by_repo = (await client.get("/admin/api/jobs", params={"repository": full_name}, headers=headers)).json()
    assert by_repo["total_count"] == 4


@pytest.mark.asyncio
async def test_admin_actions_pages_and_shows_finished_runs_on_request(client, admin_token, db_session):
    from app.models.actions import WorkflowRun
    full_name, runs = await _seed_runs_with_jobs(client, admin_token, db_session)
    runs[0].status, runs[0].conclusion = "completed", "success"
    await db_session.commit()
    headers = auth_headers(admin_token)
    active = (await client.get("/admin/api/actions", headers=headers)).json()
    assert active["total_count"] == 2 and all(r["status"] == "in_progress" for r in active["items"])
    everything = (await client.get("/admin/api/actions", params={"scope": "all", "per_page": 2}, headers=headers)).json()
    assert everything["total_count"] == 3 and len(everything["items"]) == 2
    finished = (await client.get("/admin/api/actions", params={"status": "completed"}, headers=headers)).json()
    assert [r["conclusion"] for r in finished["items"]] == ["success"]
    scoped = (await client.get("/admin/api/actions", params={"scope": "all", "repository": full_name}, headers=headers)).json()
    assert scoped["total_count"] == 3


@pytest.mark.asyncio
async def test_admin_can_requeue_a_stranded_job_and_fail_a_lost_one(client, admin_token, db_session):
    from app.models.actions import WorkflowJob, WorkflowRun
    _full_name, runs = await _seed_runs_with_jobs(client, admin_token, db_session)
    headers = auth_headers(admin_token)
    running = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.name == "running")
    )).scalar_one()
    done = (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.name == "done", WorkflowJob.run_id == runs[-1].id)
    )).scalar_one()
    running_id, done_id, run_id = running.id, done.id, runs[-1].id

    # Requeue drops the claim; a finished job is refused.
    assert (await client.post(f"/admin/api/jobs/{running_id}/requeue", headers=headers)).status_code == 204
    assert (await client.post(f"/admin/api/jobs/{done_id}/requeue", headers=headers)).status_code == 409
    db_session.expire_all()
    running = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.id == running_id))).scalar_one()
    assert running.status == "queued" and running.runner_name is None
    assert all(step["status"] == "queued" for step in running.steps)

    # Fail-as-lost: back to in progress with a step underway, then settle it.
    running.status, running.runner_name = "in_progress", "fullsend-agent-runner"
    running.steps = [{"number": 1, "name": "checkout", "status": "completed", "conclusion": "success"},
                     {"number": 2, "name": "agent", "status": "in_progress", "conclusion": None}]
    await db_session.commit()
    assert (await client.post(f"/admin/api/jobs/{running_id}/fail", headers=headers)).status_code == 204
    assert (await client.post(f"/admin/api/jobs/{done_id}/fail", headers=headers)).status_code == 409
    db_session.expire_all()
    running = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.id == running_id))).scalar_one()
    assert running.status == "completed" and running.conclusion == "failure"
    assert "lost communication" in running.steps[1]["message"]
    listed = (await client.get("/admin/api/jobs", params={"status": "completed", "runner": "fullsend-agent-runner"}, headers=headers)).json()
    assert listed["items"][0]["current_step"]["message"].startswith("The self-hosted runner lost communication")
    assert (await client.post("/admin/api/jobs/999999/fail", headers=headers)).status_code == 404
