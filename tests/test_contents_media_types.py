"""GET contents and GET readme in the raw media type, and files over 1 MiB.

Recorded on github.com by bt-f3 (waynesun09/tmp-emu-contents-bt-f3):
- Accept application/vnd.github.raw, .v3.raw or .raw+json: a file answers
  200 with its bytes and that type as the Content-Type; a directory still
  answers the JSON listing; a missing path answers the JSON 404.
- In the JSON media type, a file of 1 MiB or more has "encoding": "none" and
  "content": "", with its real size and sha; the raw media type still
  returns all of its bytes.
"""

import base64

import pytest

from tests.conftest import API, auth_headers

RAW_TYPES = [
    "application/vnd.github.raw",
    "application/vnd.github.v3.raw",
    "application/vnd.github.raw+json",
]
BINARY = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x01\x02\x03"
LARGE = b"0" * 63 + b"\n"
LARGE = LARGE * (1536 * 1024 // len(LARGE))  # 1.5 MiB


async def _put(client, h, path, content):
    resp = await client.put(
        f"{API}/repos/testuser/media/contents/{path}", headers=h,
        json={"message": f"add {path}", "content": base64.b64encode(content).decode()},
    )
    assert resp.status_code == 201, resp.text


@pytest.fixture
async def media_repo(client, test_token):
    h = auth_headers(test_token)
    resp = await client.post(f"{API}/user/repos", json={"name": "media", "auto_init": True}, headers=h)
    assert resp.status_code == 201, resp.text
    await _put(client, h, "config.yaml", b"agents: main\n")
    await _put(client, h, "bin.png", BINARY)
    await _put(client, h, "big.txt", LARGE)
    await _put(client, h, "harness/code.yaml", b"code: true\n")
    return h


@pytest.mark.asyncio
@pytest.mark.parametrize("media", RAW_TYPES)
@pytest.mark.parametrize("path, content", [("config.yaml", b"agents: main\n"), ("bin.png", BINARY), ("big.txt", LARGE)])
async def test_a_file_in_the_raw_media_type_is_its_bytes(client, media_repo, media, path, content):
    resp = await client.get(
        f"{API}/repos/testuser/media/contents/{path}", headers={**media_repo, "Accept": media})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == media
    assert resp.content == content


@pytest.mark.asyncio
@pytest.mark.parametrize("media", RAW_TYPES)
async def test_a_directory_in_the_raw_media_type_is_still_the_json_listing(client, media_repo, media):
    url = f"{API}/repos/testuser/media/contents/harness"
    raw = await client.get(url, headers={**media_repo, "Accept": media})
    plain = await client.get(url, headers=media_repo)
    assert raw.status_code == 200
    assert raw.headers["content-type"] == "application/json"
    assert raw.content == plain.content
    assert [entry["path"] for entry in raw.json()] == ["harness/code.yaml"]


@pytest.mark.asyncio
@pytest.mark.parametrize("media", RAW_TYPES)
async def test_a_missing_path_in_the_raw_media_type_is_the_json_404(client, media_repo, media):
    resp = await client.get(
        f"{API}/repos/testuser/media/contents/nope.md", headers={**media_repo, "Accept": media})
    assert resp.status_code == 404
    assert resp.headers["content-type"] == "application/json"
    assert resp.json()["message"] == "Not Found"


@pytest.mark.asyncio
async def test_the_readme_in_the_raw_media_type_is_its_bytes(client, media_repo):
    resp = await client.get(
        f"{API}/repos/testuser/media/readme",
        headers={**media_repo, "Accept": "application/vnd.github.raw+json"})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/vnd.github.raw+json"
    assert resp.content == b"# media\n"


@pytest.mark.asyncio
async def test_a_file_over_1_mib_has_no_content_in_json(client, media_repo):
    resp = await client.get(f"{API}/repos/testuser/media/contents/big.txt", headers=media_repo)
    assert resp.status_code == 200
    body = resp.json()
    assert body["size"] == len(LARGE)
    assert body["encoding"] == "none"
    assert body["content"] == ""
    blob = await client.get(f"{API}/repos/testuser/media/git/blobs/{body['sha']}", headers=media_repo)
    assert blob.status_code == 200
    assert blob.json()["size"] == len(LARGE)


@pytest.mark.asyncio
@pytest.mark.parametrize("size, encoding", [(1024 * 1024 - 1, "base64"), (1024 * 1024, "none")])
async def test_the_json_content_limit_starts_at_exactly_1_mib(client, media_repo, size, encoding):
    # github.com, probed by bt-f3: 1,048,575 bytes is base64, 1,048,576 is "none".
    await _put(client, media_repo, "edge.bin", b"x" * size)
    resp = await client.get(f"{API}/repos/testuser/media/contents/edge.bin", headers=media_repo)
    body = resp.json()
    assert body["size"] == size
    assert body["encoding"] == encoding
    if encoding == "base64":
        assert base64.b64decode(body["content"]) == b"x" * size
    else:
        assert body["content"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["config.yaml%00", "config%00.yaml", "config.yaml%0D"])
async def test_a_path_with_an_encoded_nul_or_cr_is_404(client, media_repo, path):
    resp = await client.get(f"{API}/repos/testuser/media/contents/{path}", headers=media_repo)
    assert resp.status_code == 404
