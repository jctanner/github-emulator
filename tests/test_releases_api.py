"""Releases, and the assets they carry.

The emulator had release rows but no way to put bytes on one. Fullsend's
install action resolves a workflow commit to a release tag through the tags
API and then runs ``gh release download`` on the matching asset; without
assets every install fell through to a source build the local stack cannot
complete. These cover the shape that path depends on: the tag exists once
the release does, the asset is listed on the release, and the API asset URL
returns the bytes when the client accepts an octet stream.
"""

import os

import pytest

from app.config import settings
from tests.conftest import auth_headers

API = "/api/v3"
pytestmark = pytest.mark.asyncio


async def _head_sha(client, token, owner, repo):
    resp = await client.get(f"{API}/repos/{owner}/{repo}/commits/main", headers=auth_headers(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["sha"]


async def _create_release(client, token, owner, repo, tag="v0.0.1", **extra):
    resp = await client.post(
        f"{API}/repos/{owner}/{repo}/releases",
        json={"tag_name": tag, "name": tag, **extra},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _upload(client, token, owner, repo, release_id, name, content=b"binary-bytes", content_type="application/gzip"):
    return await client.post(
        f"{API}/repos/{owner}/{repo}/releases/{release_id}/assets",
        params={"name": name},
        content=content,
        headers={**auth_headers(token), "Content-Type": content_type},
    )


async def test_creating_a_release_creates_its_tag(client, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    sha = await _head_sha(client, test_token, owner, repo)
    release = await _create_release(client, test_token, owner, repo)
    assert release["tag_name"] == "v0.0.1"
    assert release["target_commitish"] == "main"

    tags = (await client.get(f"{API}/repos/{owner}/{repo}/tags", headers=auth_headers(test_token))).json()
    assert [(t["name"], t["commit"]["sha"]) for t in tags] == [("v0.0.1", sha)]


async def test_a_release_at_a_commit_that_does_not_exist_is_refused(client, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    resp = await client.post(
        f"{API}/repos/{owner}/{repo}/releases",
        json={"tag_name": "v9.9.9", "target_commitish": "no-such-branch"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 422
    assert "no-such-branch" in resp.text


async def test_an_existing_tag_is_left_where_it_is(client, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    sha = await _head_sha(client, test_token, owner, repo)
    await client.post(
        f"{API}/repos/{owner}/{repo}/git/refs",
        json={"ref": "refs/tags/v0.0.2", "sha": sha},
        headers=auth_headers(test_token),
    )
    await _create_release(client, test_token, owner, repo, tag="v0.0.2")
    tags = (await client.get(f"{API}/repos/{owner}/{repo}/tags", headers=auth_headers(test_token))).json()
    assert [t["name"] for t in tags] == ["v0.0.2"]


async def test_latest_and_by_tag_are_reachable(client, test_repo_with_init, test_token):
    """Both used to be shadowed by /releases/{release_id}."""
    owner, repo, _ = test_repo_with_init
    await _create_release(client, test_token, owner, repo, tag="v0.0.1")
    second = await _create_release(client, test_token, owner, repo, tag="v0.0.2")
    latest = await client.get(f"{API}/repos/{owner}/{repo}/releases/latest", headers=auth_headers(test_token))
    assert latest.status_code == 200
    assert latest.json()["id"] == second["id"]
    by_tag = await client.get(f"{API}/repos/{owner}/{repo}/releases/tags/v0.0.1", headers=auth_headers(test_token))
    assert by_tag.status_code == 200
    assert by_tag.json()["tag_name"] == "v0.0.1"


async def test_an_uploaded_asset_is_listed_and_downloadable(client, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    release = await _create_release(client, test_token, owner, repo)
    resp = await _upload(client, test_token, owner, repo, release["id"], "fullsend_0.0.1_linux_amd64.tar.gz")
    assert resp.status_code == 201, resp.text
    asset = resp.json()
    assert asset["name"] == "fullsend_0.0.1_linux_amd64.tar.gz"
    assert asset["size"] == len(b"binary-bytes")
    assert asset["content_type"] == "application/gzip"
    assert asset["state"] == "uploaded"
    assert asset["url"].endswith(f"/releases/assets/{asset['id']}")
    assert asset["browser_download_url"] == (
        f"http://testserver/{owner}/{repo}/releases/download/v0.0.1/fullsend_0.0.1_linux_amd64.tar.gz"
    )
    assert os.path.isfile(os.path.join(settings.DATA_DIR, "release-assets", str(asset["id"])))

    # On the release, and on its asset listing.
    shown = (await client.get(f"{API}/repos/{owner}/{repo}/releases/{release['id']}", headers=auth_headers(test_token))).json()
    assert [a["id"] for a in shown["assets"]] == [asset["id"]]
    listed = (await client.get(f"{API}/repos/{owner}/{repo}/releases/{release['id']}/assets", headers=auth_headers(test_token))).json()
    assert [a["name"] for a in listed] == ["fullsend_0.0.1_linux_amd64.tar.gz"]

    # JSON by default, bytes for an octet-stream client. gh sends the latter.
    meta = await client.get(f"{API}/repos/{owner}/{repo}/releases/assets/{asset['id']}", headers=auth_headers(test_token))
    assert meta.status_code == 200
    assert meta.json()["download_count"] == 0
    download = await client.get(
        f"{API}/repos/{owner}/{repo}/releases/assets/{asset['id']}",
        headers={**auth_headers(test_token), "Accept": "application/octet-stream"},
    )
    assert download.status_code == 200
    assert download.content == b"binary-bytes"
    assert download.headers["content-type"].startswith("application/gzip")
    meta = await client.get(f"{API}/repos/{owner}/{repo}/releases/assets/{asset['id']}", headers=auth_headers(test_token))
    assert meta.json()["download_count"] == 1

    # The browser URL, on the web host.
    browser = await client.get(asset["browser_download_url"], headers=auth_headers(test_token))
    assert browser.status_code == 200
    assert browser.content == b"binary-bytes"


async def test_a_duplicate_asset_name_is_refused_as_github_does(client, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    release = await _create_release(client, test_token, owner, repo)
    assert (await _upload(client, test_token, owner, repo, release["id"], "x.tar.gz")).status_code == 201
    resp = await _upload(client, test_token, owner, repo, release["id"], "x.tar.gz")
    assert resp.status_code == 422
    assert "already_exists" in resp.text


async def test_an_asset_needs_a_name(client, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    release = await _create_release(client, test_token, owner, repo)
    resp = await client.post(
        f"{API}/repos/{owner}/{repo}/releases/{release['id']}/assets",
        content=b"x", headers=auth_headers(test_token),
    )
    assert resp.status_code == 422


async def test_uploading_needs_authentication(client, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    release = await _create_release(client, test_token, owner, repo)
    resp = await client.post(
        f"{API}/repos/{owner}/{repo}/releases/{release['id']}/assets", params={"name": "x"}, content=b"x",
    )
    assert resp.status_code == 401


async def test_renaming_an_asset_moves_its_browser_url(client, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    release = await _create_release(client, test_token, owner, repo)
    asset = (await _upload(client, test_token, owner, repo, release["id"], "old.tar.gz")).json()
    resp = await client.patch(
        f"{API}/repos/{owner}/{repo}/releases/assets/{asset['id']}",
        json={"name": "new.tar.gz", "label": "CLI"},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "new.tar.gz"
    assert resp.json()["label"] == "CLI"
    assert resp.json()["browser_download_url"].endswith("/releases/download/v0.0.1/new.tar.gz")


async def test_deleting_an_asset_removes_its_bytes(client, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    release = await _create_release(client, test_token, owner, repo)
    asset = (await _upload(client, test_token, owner, repo, release["id"], "x.tar.gz")).json()
    path = os.path.join(settings.DATA_DIR, "release-assets", str(asset["id"]))
    assert os.path.isfile(path)
    resp = await client.delete(f"{API}/repos/{owner}/{repo}/releases/assets/{asset['id']}", headers=auth_headers(test_token))
    assert resp.status_code == 204
    assert not os.path.exists(path)
    assert (await client.get(f"{API}/repos/{owner}/{repo}/releases/assets/{asset['id']}", headers=auth_headers(test_token))).status_code == 404


async def test_deleting_a_release_removes_its_assets(client, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    release = await _create_release(client, test_token, owner, repo)
    asset = (await _upload(client, test_token, owner, repo, release["id"], "x.tar.gz")).json()
    path = os.path.join(settings.DATA_DIR, "release-assets", str(asset["id"]))
    resp = await client.delete(f"{API}/repos/{owner}/{repo}/releases/{release['id']}", headers=auth_headers(test_token))
    assert resp.status_code == 204
    assert not os.path.exists(path)
    assert (await client.get(f"{API}/repos/{owner}/{repo}/releases/assets/{asset['id']}", headers=auth_headers(test_token))).status_code == 404


async def test_an_asset_of_another_repository_is_not_reachable_through_this_one(client, test_repo_with_init, test_token):
    owner, repo, _ = test_repo_with_init
    release = await _create_release(client, test_token, owner, repo)
    asset = (await _upload(client, test_token, owner, repo, release["id"], "x.tar.gz")).json()
    other = await client.post(f"{API}/user/repos", json={"name": "other", "auto_init": True}, headers=auth_headers(test_token))
    assert other.status_code == 201
    resp = await client.get(f"{API}/repos/{owner}/other/releases/assets/{asset['id']}", headers=auth_headers(test_token))
    assert resp.status_code == 404


async def test_the_install_actions_lookup_sequence(client, test_repo_with_init, test_token):
    """What install-fullsend-cli does with a workflow SHA, end to end."""
    owner, repo, _ = test_repo_with_init
    sha = await _head_sha(client, test_token, owner, repo)
    release = await _create_release(client, test_token, owner, repo, tag="v0.0.1")
    await _upload(client, test_token, owner, repo, release["id"], "fullsend_0.0.1_linux_amd64.tar.gz", content=b"tarball")

    # 1. `gh api repos/.../git/tags/<sha>` (annotated-tag dereference): 404 for a commit.
    assert (await client.get(f"{API}/repos/{owner}/{repo}/git/tags/{sha}", headers=auth_headers(test_token))).status_code == 404
    # 2. `gh api --paginate repos/.../tags`: the tag whose commit is the SHA.
    tags = (await client.get(f"{API}/repos/{owner}/{repo}/tags", params={"per_page": 100}, headers=auth_headers(test_token))).json()
    assert [t["name"] for t in tags if t["commit"]["sha"] == sha] == ["v0.0.1"]
    # 3. `gh release download v0.0.1 -p <asset>`: the release by tag, then the asset by API URL.
    by_tag = (await client.get(f"{API}/repos/{owner}/{repo}/releases/tags/v0.0.1", headers=auth_headers(test_token))).json()
    (asset,) = [a for a in by_tag["assets"] if a["name"] == "fullsend_0.0.1_linux_amd64.tar.gz"]
    download = await client.get(asset["url"], headers={**auth_headers(test_token), "Accept": "application/octet-stream"})
    assert download.status_code == 200
    assert download.content == b"tarball"
