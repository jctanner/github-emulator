"""``pull_request`` runs beside ``pull_request_target``.

On GitHub one pull request activity runs both kinds of workflow. A
``pull_request`` run runs the workflow in the pull request's merge commit,
``refs/pull/N/merge``, with ``GITHUB_SHA`` that commit and ``GITHUB_REF``
that ref; a fork's pull request gets a read-only token, no secrets and no
OIDC token; a pull request that does not merge cleanly runs no
``pull_request`` workflow. The emulator raised only ``pull_request_target``,
so a workflow on ``pull_request`` (fullsend's shim, for one) never ran.
"""

import base64
import os
import subprocess

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.graphql.authz import _job_grants
from app.models.actions import Secret, WorkflowJob, WorkflowRun
from app.models.repository import Repository
from app.services.job_token_service import issue_job_token
from app.services.workflow_service import build_run_expression_context
from tests.conftest import API, auth_headers

ORG = "pr-org"

PR_WORKFLOW = """name: on-pr
on:
  pull_request:
    types: [opened, labeled, synchronize]
jobs:
  check:
    runs-on: ubuntu-latest
    permissions:
      contents: write
      id-token: write
    steps:
      - run: echo pr
"""

TARGET_WORKFLOW = """name: on-target
on:
  pull_request_target:
    types: [opened, labeled, synchronize]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - run: echo target
"""


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


async def _put(client, headers, repo_full, path, text, branch="main", message=None):
    put = await client.put(
        f"{API}/repos/{repo_full}/contents/{path}", headers=headers,
        json={"message": message or f"add {path}", "content": _b64(text), "branch": branch},
    )
    assert put.status_code in (200, 201), put.text
    return put.json()["commit"]["sha"]


async def _branch(client, headers, repo_full, branch):
    main = (await client.get(f"{API}/repos/{repo_full}/git/ref/heads/main", headers=headers)).json()["object"]["sha"]
    ref = await client.post(f"{API}/repos/{repo_full}/git/refs", headers=headers,
                            json={"ref": f"refs/heads/{branch}", "sha": main})
    assert ref.status_code == 201, ref.text


@pytest_asyncio.fixture
async def base(client, test_user, test_token):
    headers = auth_headers(test_token)
    assert (await client.post(f"{API}/orgs", json={"login": ORG}, headers=headers)).status_code == 201
    created = await client.post(f"{API}/orgs/{ORG}/repos", json={"name": "base", "auto_init": True}, headers=headers)
    assert created.status_code == 201, created.text
    await _put(client, headers, f"{ORG}/base", ".github/workflows/pr.yml", PR_WORKFLOW)
    await _put(client, headers, f"{ORG}/base", ".github/workflows/target.yml", TARGET_WORKFLOW)
    return created.json()


async def _runs(db_session, event):
    return (await db_session.execute(
        select(WorkflowRun).where(WorkflowRun.event == event).order_by(WorkflowRun.id)
        .execution_options(populate_existing=True)
    )).scalars().all()


async def _disk_path(db_session, full_name):
    return (await db_session.execute(
        select(Repository.disk_path).where(Repository.full_name == full_name)
    )).scalar_one()


def _rev_parse(disk_path, ref):
    out = subprocess.run(["git", "rev-parse", "--verify", ref], env={**os.environ, "GIT_DIR": disk_path},
                         capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else None


def _parents(disk_path, sha):
    out = subprocess.run(["git", "rev-list", "--parents", "-n", "1", sha],
                         env={**os.environ, "GIT_DIR": disk_path}, capture_output=True, text=True, check=True)
    return out.stdout.split()[1:]


async def _open(client, headers, branch, title="change"):
    pull = await client.post(f"{API}/repos/{ORG}/base/pulls", headers=headers,
                             json={"title": title, "head": branch, "base": "main"})
    assert pull.status_code == 201, pull.text
    return pull.json()


@pytest.mark.asyncio
async def test_opening_a_pull_request_runs_both_events(client, base, test_token, db_session):
    headers = auth_headers(test_token)
    await _branch(client, headers, f"{ORG}/base", "topic")
    head_sha = await _put(client, headers, f"{ORG}/base", "topic.txt", "topic\n", branch="topic")
    pull = await _open(client, headers, "topic")
    number = pull["number"]

    target = await _runs(db_session, "pull_request_target")
    assert len(target) == 1
    runs = await _runs(db_session, "pull_request")
    assert len(runs) == 1, "the pull_request workflow runs too"
    run = runs[0]

    disk = await _disk_path(db_session, f"{ORG}/base")
    merge_sha = _rev_parse(disk, f"refs/pull/{number}/merge")
    assert merge_sha and _rev_parse(disk, f"refs/pull/{number}/head") == head_sha
    main_sha = _rev_parse(disk, "refs/heads/main")
    assert _parents(disk, merge_sha) == [main_sha, head_sha]
    # GITHUB_SHA and GITHUB_REF are the merge commit and its ref; the run's
    # branch is the pull request's head branch.
    assert run.head_sha == merge_sha
    assert run.trigger_payload["ref"] == f"refs/pull/{number}/merge"
    assert run.head_branch == "topic"
    assert run.trigger_payload["pull_request"]["number"] == number
    # pull_request_target is unchanged: the base branch, its own commit.
    assert target[0].head_sha != merge_sha
    assert target[0].trigger_payload["ref"] == "refs/heads/main"


@pytest.mark.asyncio
async def test_the_workflow_is_read_from_the_merge_commit(client, base, test_token, db_session):
    headers = auth_headers(test_token)
    await _branch(client, headers, f"{ORG}/base", "new-workflow")
    added = PR_WORKFLOW.replace("name: on-pr", "name: only-in-the-pull-request")
    await _put(client, headers, f"{ORG}/base", ".github/workflows/added.yml", added, branch="new-workflow")
    await _open(client, headers, "new-workflow")

    names = sorted(run.workflow.name for run in await _runs(db_session, "pull_request"))
    assert names == ["on-pr", "only-in-the-pull-request"]
    # pull_request_target reads the base branch, which has no such workflow.
    assert [run.workflow.name for run in await _runs(db_session, "pull_request_target")] == ["on-target"]


@pytest.mark.asyncio
async def test_labeling_and_pushing_run_pull_request_again(client, base, test_token, db_session):
    headers = auth_headers(test_token)
    await _branch(client, headers, f"{ORG}/base", "topic")
    await _put(client, headers, f"{ORG}/base", "topic.txt", "topic\n", branch="topic")
    number = (await _open(client, headers, "topic"))["number"]

    labeled = await client.post(f"{API}/repos/{ORG}/base/issues/{number}/labels", headers=headers,
                                json={"labels": ["ready-for-review"]})
    assert labeled.status_code == 200, labeled.text
    pushed = await _put(client, headers, f"{ORG}/base", "more.txt", "more\n", branch="topic")

    runs = await _runs(db_session, "pull_request")
    assert len(runs) == 3  # opened, labeled, synchronize
    disk = await _disk_path(db_session, f"{ORG}/base")
    assert _rev_parse(disk, f"refs/pull/{number}/head") == pushed
    last = runs[-1]
    assert last.head_sha == _rev_parse(disk, f"refs/pull/{number}/merge")
    assert pushed in _parents(disk, last.head_sha)
    assert len(await _runs(db_session, "pull_request_target")) == 3


@pytest.mark.asyncio
async def test_a_conflicting_pull_request_runs_no_pull_request_workflow(client, base, test_token, db_session):
    headers = auth_headers(test_token)
    await _branch(client, headers, f"{ORG}/base", "clash")
    await _put(client, headers, f"{ORG}/base", "same.txt", "from the branch\n", branch="clash")
    await _put(client, headers, f"{ORG}/base", "same.txt", "from main\n", branch="main")
    number = (await _open(client, headers, "clash"))["number"]

    assert await _runs(db_session, "pull_request") == []
    assert len(await _runs(db_session, "pull_request_target")) == 1
    disk = await _disk_path(db_session, f"{ORG}/base")
    assert _rev_parse(disk, f"refs/pull/{number}/merge") is None


@pytest.mark.asyncio
async def test_graphql_create_pull_request_runs_pull_request(client, base, test_token, db_session):
    headers = auth_headers(test_token)
    await _branch(client, headers, f"{ORG}/base", "gql")
    await _put(client, headers, f"{ORG}/base", "gql.txt", "gql\n", branch="gql")
    query = """mutation($input: CreatePullRequestInput!) {
      createPullRequest(input: $input) { pullRequest { number } } }"""
    created = await client.post("/api/graphql", headers=headers, json={
        "query": query,
        "variables": {"input": {"repositoryId": base["node_id"], "headRefName": "gql",
                                "baseRefName": "main", "title": "via graphql"}},
    })
    assert "errors" not in created.json(), created.text
    runs = await _runs(db_session, "pull_request")
    assert len(runs) == 1
    assert runs[0].trigger_payload["pull_request"]["head"]["repo"]["id"] == base["id"]


async def _fork_pull_request(client, headers, base):
    fork = await client.post(f"{API}/repos/{ORG}/base/forks", json={"name": "base-fork", "organization": ORG},
                             headers=headers)
    assert fork.status_code == 202, fork.text
    fork_full = fork.json()["full_name"]
    await _branch(client, headers, fork_full, "from-fork")
    await _put(client, headers, fork_full, "fork.txt", "fork\n", branch="from-fork")
    query = """mutation($input: CreatePullRequestInput!) {
      createPullRequest(input: $input) { pullRequest { number } } }"""
    created = await client.post("/api/graphql", headers=headers, json={
        "query": query,
        "variables": {"input": {"repositoryId": base["node_id"], "headRepositoryId": fork.json()["node_id"],
                                "headRefName": "from-fork", "baseRefName": "main", "title": "from a fork"}},
    })
    assert "errors" not in created.json(), created.text
    return created.json()["data"]["createPullRequest"]["pullRequest"]["number"]


async def _repository(db_session, repo_id):
    return (await db_session.execute(select(Repository).where(Repository.id == repo_id))).scalar_one()


@pytest.mark.asyncio
async def test_a_forks_pull_request_run_gets_no_secrets_and_a_read_only_token(
    client, base, test_token, db_session,
):
    headers = auth_headers(test_token)
    db_session.add(Secret(repo_id=base["id"], name="DEPLOY_KEY", value="s3cret"))
    await db_session.commit()
    number = await _fork_pull_request(client, headers, base)

    (run,) = await _runs(db_session, "pull_request")
    assert run.trigger_payload["pull_request"]["head"]["repo"]["id"] != base["id"]
    disk = await _disk_path(db_session, f"{ORG}/base")
    assert run.head_sha == _rev_parse(disk, f"refs/pull/{number}/merge")
    context = await build_run_expression_context(db_session, run)
    assert context["secrets"] == {}
    (target,) = await _runs(db_session, "pull_request_target")
    assert (await build_run_expression_context(db_session, target))["secrets"] == {"DEPLOY_KEY": "s3cret"}

    job = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.run_id == run.id))).scalars().first()
    token = {"Authorization": f"Bearer {issue_job_token(job)}"}
    # The job asked for contents: write; a fork's run reads only.
    assert (await client.get(f"{API}/repos/{ORG}/base/contents/README.md", headers=token)).status_code == 200
    write = await client.put(f"{API}/repos/{ORG}/base/contents/x.txt", headers=token,
                             json={"message": "x", "content": _b64("x\n"), "branch": "main"})
    assert write.status_code == 403, write.text
    # GraphQL's own scope check refuses its writes too.
    assert await _job_grants(db_session, job, await _repository(db_session, base["id"]), ("contents",)) is False
    # ... and no OIDC token, though it asked for id-token: write.
    oidc = await client.get("/actions/oidc/token?audience=x", headers=token)
    assert oidc.status_code == 403, oidc.text


@pytest.mark.asyncio
async def test_a_same_repository_pull_request_run_keeps_its_token_and_secrets(
    client, base, test_token, db_session,
):
    headers = auth_headers(test_token)
    db_session.add(Secret(repo_id=base["id"], name="DEPLOY_KEY", value="s3cret"))
    await db_session.commit()
    await _branch(client, headers, f"{ORG}/base", "topic")
    await _put(client, headers, f"{ORG}/base", "topic.txt", "topic\n", branch="topic")
    await _open(client, headers, "topic")

    (run,) = await _runs(db_session, "pull_request")
    assert (await build_run_expression_context(db_session, run))["secrets"] == {"DEPLOY_KEY": "s3cret"}
    job = (await db_session.execute(select(WorkflowJob).where(WorkflowJob.run_id == run.id))).scalars().first()
    token = {"Authorization": f"Bearer {issue_job_token(job)}"}
    write = await client.put(f"{API}/repos/{ORG}/base/contents/x.txt", headers=token,
                             json={"message": "x", "content": _b64("x\n"), "branch": "topic"})
    assert write.status_code in (200, 201), write.text
    assert await _job_grants(db_session, job, await _repository(db_session, base["id"]), ("contents",)) is True


GUARDED_WORKFLOW = """name: guarded
on:
  pull_request:
    types: [opened]
jobs:
  dispatch:
    if: >-
      github.event_name != 'pull_request'
      || github.event.pull_request.head.repo.id == github.repository_id
    runs-on: ubuntu-latest
    steps:
      - run: echo same repository only
"""


async def _guarded_job(db_session):
    runs = [run for run in await _runs(db_session, "pull_request") if run.workflow.name == "guarded"]
    assert len(runs) == 1
    return (await db_session.execute(
        select(WorkflowJob).where(WorkflowJob.run_id == runs[0].id)
    )).scalars().one()


@pytest.mark.asyncio
async def test_the_same_repository_guard_runs_for_a_branch(client, base, test_token, db_session):
    # Fullsend's shim runs its dispatch job on pull_request only when
    # head.repo.id == github.repository_id (a number and a string, equal).
    headers = auth_headers(test_token)
    await _put(client, headers, f"{ORG}/base", ".github/workflows/guarded.yml", GUARDED_WORKFLOW)
    await _branch(client, headers, f"{ORG}/base", "topic")
    await _put(client, headers, f"{ORG}/base", "topic.txt", "topic\n", branch="topic")
    await _open(client, headers, "topic")
    job = await _guarded_job(db_session)
    assert job.conclusion != "skipped", "a same-repository pull request passes the guard"


@pytest.mark.asyncio
async def test_the_same_repository_guard_skips_a_forks_pull_request(client, base, test_token, db_session):
    headers = auth_headers(test_token)
    await _put(client, headers, f"{ORG}/base", ".github/workflows/guarded.yml", GUARDED_WORKFLOW)
    await _fork_pull_request(client, headers, base)
    job = await _guarded_job(db_session)
    assert job.conclusion == "skipped"
