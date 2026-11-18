"""REST ``GET /repos/{owner}/{repo}/compare/{base}...{head}``.

GitHub answers with the base and merge-base commits as full commit objects,
the ahead/behind counts, the commits on head since the merge base (oldest
first), the files changed from the merge base to head, and a ``status`` of
``ahead``, ``behind``, ``diverged`` or ``identical``. Either side may be a
branch, a SHA, or ``owner:branch`` naming a fork in the network.
"""

import base64

import pytest
import pytest_asyncio

from tests.conftest import API, auth_headers

REPO = "testuser/compare-repo"


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


async def _put(client, token, repo_full, path, text, branch):
    resp = await client.put(
        f"{API}/repos/{repo_full}/contents/{path}", headers=auth_headers(token),
        json={"message": f"add {path}", "content": _b64(text), "branch": branch},
    )
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["commit"]["sha"]


async def _branch(client, token, repo_full, name, sha):
    resp = await client.post(
        f"{API}/repos/{repo_full}/git/refs", headers=auth_headers(token),
        json={"ref": f"refs/heads/{name}", "sha": sha},
    )
    assert resp.status_code == 201, resp.text


async def _main_sha(client, token, repo_full):
    resp = await client.get(f"{API}/repos/{repo_full}/git/ref/heads/main", headers=auth_headers(token))
    return resp.json()["object"]["sha"]


@pytest_asyncio.fixture
async def repo(client, test_user, test_token):
    resp = await client.post(
        f"{API}/user/repos", json={"name": "compare-repo", "auto_init": True},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 201, resp.text
    return REPO


@pytest.mark.asyncio
async def test_compare_reports_the_merge_base_and_commits_ahead(client, test_token, repo):
    main = await _main_sha(client, test_token, repo)
    await _branch(client, test_token, repo, "feat", main)
    head = await _put(client, test_token, repo, "feature.txt", "new\n", "feat")

    resp = await client.get(f"{API}/repos/{repo}/compare/main...feat", headers=auth_headers(test_token))

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "ahead"
    assert data["ahead_by"] == 1 and data["behind_by"] == 0 and data["total_commits"] == 1
    assert data["base_commit"]["sha"] == main
    assert data["merge_base_commit"]["sha"] == main
    assert data["merge_base_commit"]["commit"]["tree"]["sha"]
    assert isinstance(data["merge_base_commit"]["parents"], list)
    assert [c["sha"] for c in data["commits"]] == [head]
    assert data["commits"][0]["parents"][0]["sha"] == main
    assert [f["filename"] for f in data["files"]] == ["feature.txt"]
    assert data["files"][0]["status"] == "added" and data["files"][0]["additions"] == 1


@pytest.mark.asyncio
async def test_compare_reports_behind_diverged_and_identical(client, test_token, repo):
    main = await _main_sha(client, test_token, repo)
    await _branch(client, test_token, repo, "feat", main)
    feat = await _put(client, test_token, repo, "feature.txt", "new\n", "feat")

    reverse = (await client.get(f"{API}/repos/{repo}/compare/feat...main", headers=auth_headers(test_token))).json()
    assert reverse["status"] == "behind"
    assert (reverse["ahead_by"], reverse["behind_by"], reverse["total_commits"]) == (0, 1, 0)
    assert reverse["base_commit"]["sha"] == feat and reverse["merge_base_commit"]["sha"] == main
    assert reverse["commits"] == [] and reverse["files"] == []

    new_main = await _put(client, test_token, repo, "other.txt", "other\n", "main")
    diverged = (await client.get(f"{API}/repos/{repo}/compare/main...feat", headers=auth_headers(test_token))).json()
    assert diverged["status"] == "diverged"
    assert (diverged["ahead_by"], diverged["behind_by"]) == (1, 1)
    assert diverged["base_commit"]["sha"] == new_main
    assert diverged["merge_base_commit"]["sha"] == main
    # Files compare the merge base with head, so main's own change is absent.
    assert [f["filename"] for f in diverged["files"]] == ["feature.txt"]

    same = (await client.get(f"{API}/repos/{repo}/compare/{feat}...{feat}", headers=auth_headers(test_token))).json()
    assert same["status"] == "identical"
    assert (same["ahead_by"], same["behind_by"], same["total_commits"]) == (0, 0, 0)


@pytest.mark.asyncio
async def test_compare_accepts_shas_and_the_owner_prefix(client, test_token, repo):
    main = await _main_sha(client, test_token, repo)
    await _branch(client, test_token, repo, "feat", main)
    head = await _put(client, test_token, repo, "feature.txt", "new\n", "feat")

    by_sha = (await client.get(f"{API}/repos/{repo}/compare/{main}...{head}", headers=auth_headers(test_token))).json()
    assert by_sha["merge_base_commit"]["sha"] == main and by_sha["ahead_by"] == 1

    owned = await client.get(f"{API}/repos/{repo}/compare/testuser:main...testuser:feat", headers=auth_headers(test_token))
    assert owned.status_code == 200, owned.text
    assert owned.json()["merge_base_commit"]["sha"] == main


@pytest.mark.asyncio
async def test_compare_with_a_fork_branch(client, test_token):
    headers = auth_headers(test_token)
    assert (await client.post(f"{API}/orgs", json={"login": "cmp-org"}, headers=headers)).status_code == 201
    created = await client.post(f"{API}/orgs/cmp-org/repos", json={"name": "base", "auto_init": True}, headers=headers)
    assert created.status_code == 201, created.text
    fork = await client.post(f"{API}/repos/cmp-org/base/forks", json={}, headers=headers)
    assert fork.status_code == 202, fork.text
    assert fork.json()["full_name"] == "testuser/base"

    main = await _main_sha(client, test_token, "cmp-org/base")
    await _branch(client, test_token, "testuser/base", "topic", main)
    head = await _put(client, test_token, "testuser/base", "fork.txt", "from the fork\n", "topic")

    resp = await client.get(f"{API}/repos/cmp-org/base/compare/main...testuser:topic", headers=headers)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "ahead" and data["ahead_by"] == 1
    assert data["merge_base_commit"]["sha"] == main
    assert [c["sha"] for c in data["commits"]] == [head]
    assert [f["filename"] for f in data["files"]] == ["fork.txt"]


@pytest.mark.asyncio
async def test_compare_with_an_unknown_ref_is_not_found(client, test_token, repo):
    resp = await client.get(f"{API}/repos/{repo}/compare/main...nope", headers=auth_headers(test_token))
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_a_fork_side_that_is_not_a_branch_name_is_not_found(client, test_token, db_session):
    """The fork side of a compare must be a valid branch name.

    ``foo:main`` is not one, even where the fork has a file named
    ``main^{commit}`` on ``foo``; it is 404 and the base repository's refs
    are unchanged.
    """
    import subprocess
    from urllib.parse import quote

    from sqlalchemy import select

    from app.models.repository import Repository

    headers = auth_headers(test_token)
    assert (await client.post(f"{API}/orgs", json={"login": "cmp-org2"}, headers=headers)).status_code == 201
    created = await client.post(f"{API}/orgs/cmp-org2/repos", json={"name": "base", "auto_init": True}, headers=headers)
    assert created.status_code == 201, created.text
    assert (await client.post(f"{API}/repos/cmp-org2/base/forks", json={}, headers=headers)).status_code == 202

    main = await _main_sha(client, test_token, "cmp-org2/base")
    await _branch(client, test_token, "testuser/base", "foo", main)
    await _put(client, test_token, "testuser/base", quote("main^{commit}", safe=""), "x\n", "foo")

    resp = await client.get(
        f"{API}/repos/cmp-org2/base/compare/main...{quote('testuser:foo:main', safe=':')}", headers=headers
    )

    assert resp.status_code == 404, resp.text
    # The branch record is in the database, so read the bare repository itself.
    base = (await db_session.execute(
        select(Repository).where(Repository.full_name == "cmp-org2/base")
    )).scalar_one()
    on_disk = subprocess.run(
        ["git", "--git-dir", base.disk_path, "rev-parse", "refs/heads/main"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert on_disk == main


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["topic", "feature/x"])
async def test_branch_names_that_check_ref_format_accepts(name):
    from app.services.cross_repo_pulls import is_branch_name

    assert await is_branch_name(name)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["", "-x", "foo:main", "a^b", "a~1", "a..b", "a b"])
async def test_names_that_are_not_branch_names(name):
    from app.services.cross_repo_pulls import is_branch_name

    assert not await is_branch_name(name)
