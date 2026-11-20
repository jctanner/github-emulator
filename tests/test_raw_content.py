"""Raw file content at /{owner}/{repo}/raw/{ref}/{path}.

github.com serves raw bytes from a second hostname; an enterprise install
serves them from the appliance. This emulator is the second shape. Tools fetch
files this way instead of through the contents API, and without the route they
reach the public internet instead of the emulator.
"""

import base64

import pytest

from tests.conftest import API, auth_headers


async def _commit(client, token, full_name, path, text):
    url = f"{API}/repos/{full_name}/contents/{path}"
    body = {
        "message": f"add {path}",
        "content": base64.b64encode(text.encode()).decode(),
        "branch": "main",
    }
    # Updating an existing file needs its current blob sha, as on GitHub.
    current = await client.get(f"{url}?ref=main", headers=auth_headers(token))
    if current.status_code == 200:
        body["sha"] = current.json()["sha"]
    response = await client.put(url, json=body, headers=auth_headers(token))
    assert response.status_code in (200, 201), response.text
    return response.json()["commit"]["sha"]


@pytest.mark.asyncio
async def test_a_file_is_served_as_plain_bytes(client, test_token, test_repo_with_init):
    _owner, _name, repo = test_repo_with_init
    await _commit(client, test_token, repo["full_name"], "agents/triage.md", "harness\n")

    response = await client.get(f"/{repo['full_name']}/raw/main/agents/triage.md")
    assert response.status_code == 200
    assert response.text == "harness\n"
    assert response.headers["content-type"].startswith("text/plain")


@pytest.mark.asyncio
async def test_a_commit_sha_works_as_the_ref(client, test_token, test_repo_with_init):
    """What a pinned fetch uses: resolve a ref, then download at that sha."""
    _owner, _name, repo = test_repo_with_init
    sha = await _commit(client, test_token, repo["full_name"], "pinned.txt", "v1\n")
    await _commit(client, test_token, repo["full_name"], "pinned.txt", "v2\n")

    at_sha = await client.get(f"/{repo['full_name']}/raw/{sha}/pinned.txt")
    assert at_sha.status_code == 200
    assert at_sha.text == "v1\n"

    at_head = await client.get(f"/{repo['full_name']}/raw/main/pinned.txt")
    assert at_head.text == "v2\n"


@pytest.mark.asyncio
async def test_a_missing_file_is_404(client, test_repo_with_init):
    _owner, _name, repo = test_repo_with_init
    response = await client.get(f"/{repo['full_name']}/raw/main/absent.txt")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_directory_is_404(client, test_token, test_repo_with_init):
    """GitHub's raw host serves files only; a directory path is a 404."""
    _owner, _name, repo = test_repo_with_init
    await _commit(client, test_token, repo["full_name"], "agents/triage.md", "harness\n")

    response = await client.get(f"/{repo['full_name']}/raw/main/agents")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_ref_that_looks_like_an_option_is_404(client, test_repo_with_init):
    _owner, _name, repo = test_repo_with_init
    for ref in ("--all", "-p", "--output=out.txt"):
        response = await client.get(f"/{repo['full_name']}/raw/{ref}/README.md")
        assert response.status_code == 404, ref


@pytest.mark.asyncio
async def test_a_missing_repository_is_404(client):
    response = await client.get("/nobody/nothing/raw/main/README.md")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_private_repository_is_not_readable_by_an_outsider(
    client, db_session, test_token
):
    """This route is outside /repos/, so it asks the visibility question itself."""
    import hashlib
    import secrets

    from app.models.token import PersonalAccessToken
    from app.models.user import User

    created = await client.post(
        f"{API}/user/repos",
        json={"name": "raw-private", "private": True, "auto_init": True},
        headers=auth_headers(test_token),
    )
    assert created.status_code == 201
    full_name = created.json()["full_name"]
    await _commit(client, test_token, full_name, "secret.txt", "confidential\n")

    user = User(
        login="raw-outsider",
        hashed_password=hashlib.sha256(b"password").hexdigest(),
        email="raw-outsider@localhost",
        name="Raw Outsider",
        site_admin=False,
    )
    db_session.add(user)
    await db_session.flush()
    raw = f"ghp_{secrets.token_hex(20)}"
    db_session.add(
        PersonalAccessToken(
            user_id=user.id,
            name="probe",
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            token_prefix=raw[:8],
            scopes=["repo"],
        )
    )
    await db_session.commit()

    anonymous = await client.get(f"/{full_name}/raw/main/secret.txt")
    assert anonymous.status_code == 404

    outsider = await client.get(
        f"/{full_name}/raw/main/secret.txt", headers=auth_headers(raw)
    )
    assert outsider.status_code == 404

    owner = await client.get(
        f"/{full_name}/raw/main/secret.txt", headers=auth_headers(test_token)
    )
    assert owner.status_code == 200


@pytest.mark.asyncio
async def test_raw_githubusercontent_host_and_path_shape(client, test_token, test_repo_with_init):
    """A machine that resolves raw.githubusercontent.com here gets the file.

    Fullsend hard-codes that host for a URL-sourced harness.
    """
    _owner, _name, repo = test_repo_with_init
    await _commit(client, test_token, repo["full_name"], "harness/triage.yaml", "agent: triage\n")
    raw = {"Host": "raw.githubusercontent.com"}

    for ref in ("main", "refs/heads/main"):
        response = await client.get(f"/{repo['full_name']}/{ref}/harness/triage.yaml", headers=raw)
        assert response.status_code == 200, (ref, response.text)
        assert response.text == "agent: triage\n"

    missing = await client.get(f"/{repo['full_name']}/main/harness/absent.yaml", headers=raw)
    assert missing.status_code == 404
    # The emulator's own host keeps its own paths.
    assert (await client.get(f"/{repo['full_name']}/main/harness/triage.yaml")).status_code != 200


def _fake_upstream(monkeypatch, response):
    """Answer the raw route's upstream fetch with ``response``; return what it sent."""
    seen = {}

    class FakeClient:  # the test's own client was built before this patch
        def __init__(self, **kwargs):
            seen["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def stream(self, method, url, headers=None, extensions=None, **_):
            seen.update(method=method, url=url, headers=headers, extensions=extensions)

            class Streamed:
                async def __aenter__(self):
                    return response

                async def __aexit__(self, *_):
                    return False

            return Streamed()

    monkeypatch.setattr("app.api.raw_content.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr("app.api.raw_content.settings.RAW_CONTENT_UPSTREAM_ADDR", "185.199.108.133")
    return seen


@pytest.mark.asyncio
async def test_raw_host_fetches_other_repositories_from_the_real_host(
    client, test_token, test_repo_with_init, monkeypatch,
):
    """A repository the emulator does not have comes from the real host.

    On a machine that resolves raw.githubusercontent.com here, a job that
    downloads a public file (an installer, say) would otherwise get a 404.
    """
    import httpx

    seen = _fake_upstream(
        monkeypatch,
        httpx.Response(200, content=b"#!/bin/sh\n", headers={"content-type": "text/plain"}),
    )
    raw = {"Host": "raw.githubusercontent.com", "Authorization": f"token {test_token}"}

    response = await client.get("/NVIDIA/OpenShell/main/install.sh", headers=raw)
    assert response.status_code == 200
    assert response.text == "#!/bin/sh\n"
    assert seen["url"] == "https://185.199.108.133/NVIDIA/OpenShell/main/install.sh"
    assert seen["headers"] == {"Host": "raw.githubusercontent.com"}  # no credentials
    assert seen["extensions"] == {"sni_hostname": "raw.githubusercontent.com"}
    # Ambient proxy and certificate environment settings are not used.
    assert seen["client_kwargs"]["trust_env"] is False

    # The emulator's own repositories are never fetched upstream, even for a
    # path they do not have; nor is the emulator's own host proxied.
    seen.clear()
    _owner, _name, repo = test_repo_with_init
    assert (await client.get(f"/{repo['full_name']}/main/absent.txt", headers=raw)).status_code == 404
    assert (await client.get("/NVIDIA/OpenShell/raw/main/install.sh")).status_code == 404
    # Repository names are case-insensitive on GitHub: another spelling of
    # one of the emulator's own repositories is not fetched upstream either.
    upper = repo["full_name"].upper()
    assert upper != repo["full_name"]
    assert (await client.get(f"/{upper}/main/README.md", headers=raw)).status_code == 404
    assert not seen


async def _create_ref(client, token, full_name, ref, sha):
    response = await client.post(
        f"{API}/repos/{full_name}/git/refs",
        json={"ref": ref, "sha": sha},
        headers=auth_headers(token),
    )
    assert response.status_code == 201, response.text


@pytest.mark.asyncio
async def test_raw_host_explicit_refs_read_that_ref(client, test_token, test_repo_with_init):
    """refs/heads/<x> and refs/tags/<x> each read their own ref.

    A short name resolves tags first in git, so the explicit forms must not
    be reduced to it: a branch and a tag of the same name differ here.
    """
    _owner, _name, repo = test_repo_with_init
    full_name = repo["full_name"]
    tagged = await _commit(client, test_token, full_name, "notes.txt", "from the tag\n")
    await _create_ref(client, test_token, full_name, "refs/tags/release", tagged)
    branched = await _commit(client, test_token, full_name, "notes.txt", "from the branch\n")
    await _create_ref(client, test_token, full_name, "refs/heads/release", branched)
    await _create_ref(client, test_token, full_name, "refs/heads/only-branch", branched)
    raw = {"Host": "raw.githubusercontent.com"}

    heads = await client.get(f"/{full_name}/refs/heads/release/notes.txt", headers=raw)
    assert heads.status_code == 200, heads.text
    assert heads.text == "from the branch\n"

    tags = await client.get(f"/{full_name}/refs/tags/release/notes.txt", headers=raw)
    assert tags.status_code == 200, tags.text
    assert tags.text == "from the tag\n"

    # A tags URL for a name that is only a branch is not found.
    only = await client.get(f"/{full_name}/refs/tags/only-branch/notes.txt", headers=raw)
    assert only.status_code == 404
    branch = await client.get(f"/{full_name}/refs/heads/only-branch/notes.txt", headers=raw)
    assert branch.status_code == 200


@pytest.mark.asyncio
async def test_raw_host_upstream_path_stays_percent_encoded(client, monkeypatch):
    """#, ? and % in a filename reach the real host encoded, with no query string."""
    import httpx

    seen = _fake_upstream(monkeypatch, httpx.Response(200, content=b"ok\n"))
    raw = {"Host": "raw.githubusercontent.com"}

    response = await client.get("/NVIDIA/OpenShell/main/a%23b%3Fc%25d.txt?token=x", headers=raw)
    assert response.status_code == 200
    assert response.text == "ok\n"
    assert seen["url"] == "https://185.199.108.133/NVIDIA/OpenShell/main/a%23b%3Fc%25d.txt"


@pytest.mark.asyncio
async def test_raw_host_upstream_redirect_is_502(client, monkeypatch):
    """A redirect is not followed, and not passed on without its target."""
    import httpx

    _fake_upstream(
        monkeypatch,
        httpx.Response(301, headers={"location": "https://example.invalid/elsewhere"}),
    )
    raw = {"Host": "raw.githubusercontent.com"}

    response = await client.get("/NVIDIA/OpenShell/main/install.sh", headers=raw)
    assert response.status_code == 502
    assert "location" not in response.headers


@pytest.mark.asyncio
async def test_raw_host_upstream_body_over_the_cap_is_502(client, monkeypatch):
    import httpx

    _fake_upstream(monkeypatch, httpx.Response(200, content=b"x" * 11))
    raw = {"Host": "raw.githubusercontent.com"}

    monkeypatch.setattr("app.api.raw_content.settings.RAW_CONTENT_UPSTREAM_MAX_BYTES", 10)
    over = await client.get("/NVIDIA/OpenShell/main/install.sh", headers=raw)
    assert over.status_code == 502

    _fake_upstream(monkeypatch, httpx.Response(200, content=b"x" * 10))
    at_cap = await client.get("/NVIDIA/OpenShell/main/install.sh", headers=raw)
    assert at_cap.status_code == 200
    assert at_cap.content == b"x" * 10
