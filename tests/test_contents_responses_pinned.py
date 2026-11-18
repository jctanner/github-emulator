"""Contents and raw responses, pinned byte for byte.

The snapshot in tests/fixtures/contents_responses.json was recorded from the
emulator before the contents route moved from three git processes per call to
one. Every case here must keep answering exactly as recorded: a file, an
executable, binary content, a directory, the root, a symlink, a submodule, a
missing path, and refs given as a branch, a lightweight tag, an annotated tag
and a commit sha.

The repository is built with git plumbing and fixed dates, so every object
sha is the same on every run. Set UPDATE_CONTENTS_SNAPSHOT=1 to record again.
"""

import json
import os
import pathlib
import subprocess

import pytest
from sqlalchemy import select

from app.models.repository import Repository
from tests.conftest import API, auth_headers

SNAPSHOT = pathlib.Path(__file__).parent / "fixtures" / "contents_responses.json"
SUBMODULE_COMMIT = "1234567890abcdef1234567890abcdef12345678"


def _git(git_dir: str, *args: str, data: bytes | None = None) -> str:
    env = {
        **os.environ,
        "GIT_DIR": git_dir,
        "GIT_AUTHOR_NAME": "Pin", "GIT_AUTHOR_EMAIL": "pin@example.com",
        "GIT_COMMITTER_NAME": "Pin", "GIT_COMMITTER_EMAIL": "pin@example.com",
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    }
    out = subprocess.run(["git", *args], input=data, env=env, capture_output=True, check=True)
    return out.stdout.decode().strip()


def _blob(git_dir: str, content: bytes) -> str:
    return _git(git_dir, "hash-object", "-w", "--stdin", data=content)


def _tree(git_dir: str, entries: list[tuple[str, str, str, str]]) -> str:
    lines = "".join(f"{mode} {kind} {sha}\t{name}\n" for mode, kind, sha, name in entries)
    return _git(git_dir, "mktree", "--missing", data=lines.encode())


def _build(git_dir: str) -> str:
    docs = _tree(git_dir, [
        ("100644", "blob", _blob(git_dir, b"# Guide\n"), "guide.md"),
        ("100644", "blob", _blob(git_dir, b"nested\n"), "z-last.txt"),
    ])
    root = _tree(git_dir, [
        ("100644", "blob", _blob(git_dir, b"hello\n"), "README.md"),
        ("100644", "blob", _blob(git_dir, b"\x00\x01binary\xff\n"), "bin.dat"),
        ("040000", "tree", docs, "docs"),
        ("100755", "blob", _blob(git_dir, b"#!/bin/sh\necho hi\n"), "exec.sh"),
        ("120000", "blob", _blob(git_dir, b"README.md"), "link"),
        ("160000", "commit", SUBMODULE_COMMIT, "sub"),
    ])
    commit = _git(git_dir, "commit-tree", root, "-m", "pinned")
    _git(git_dir, "update-ref", "refs/heads/main", commit)
    _git(git_dir, "tag", "v1", commit)
    _git(git_dir, "tag", "-a", "v1-annotated", "-m", "annotated", commit)
    return commit


def _cases(owner: str, commit: str) -> list[str]:
    c = f"{API}/repos/{owner}/pinned/contents"
    r = f"/{owner}/pinned/raw"
    readme = f"{API}/repos/{owner}/pinned/readme"
    return [
        f"{c}/README.md",
        f"{c}/README.md?ref=main",
        f"{c}/README.md?ref=v1",
        f"{c}/README.md?ref=v1-annotated",
        f"{c}/README.md?ref={commit}",
        f"{c}/exec.sh",
        f"{c}/bin.dat",
        f"{c}/docs",
        f"{c}/docs/",
        f"{c}/docs?ref=v1",
        f"{c}/docs/guide.md?ref={commit}",
        f"{c}/",
        f"{c}/link",
        f"{c}/sub",
        f"{c}/missing.txt",
        f"{c}/docs/missing.txt",
        f"{c}/README.md?ref=no-such-ref",
        f"{c}/README.md/inner",
        f"{readme}",
        f"{readme}?ref=v1",
        f"{readme}/docs",
        f"{r}/main/README.md",
        f"{r}/v1/docs/guide.md",
        f"{r}/v1-annotated/exec.sh",
        f"{r}/{commit}/bin.dat",
        f"{r}/main/link",
        f"{r}/main/missing.txt",
        f"{r}/no-such-ref/README.md",
    ]


@pytest.mark.asyncio
async def test_contents_and_raw_responses_match_the_snapshot(client, db_session, test_token):
    h = auth_headers(test_token)
    resp = await client.post(f"{API}/user/repos", json={"name": "pinned"}, headers=h)
    assert resp.status_code == 201, resp.text
    repo = (await db_session.execute(
        select(Repository).where(Repository.full_name == "testuser/pinned")
    )).scalar_one()
    commit = _build(repo.disk_path)

    got = {}
    for url in _cases("testuser", commit):
        resp = await client.get(url, headers=h)
        got[url.replace(commit, "<commit>")] = {
            "status": resp.status_code,
            "content_type": resp.headers.get("content-type"),
            "body": resp.content.decode("latin-1"),
        }

    if os.environ.get("UPDATE_CONTENTS_SNAPSHOT") == "1":
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n")
    want = json.loads(SNAPSHOT.read_text())
    assert sorted(got) == sorted(want)
    for url in want:
        assert got[url] == want[url], url


@pytest.mark.asyncio
async def test_a_directory_lists_a_non_ascii_name_as_it_is(client, db_session, test_token):
    # git ls-tree quoted such names ("\303\251.md"); GitHub lists the name.
    h = auth_headers(test_token)
    resp = await client.post(f"{API}/user/repos", json={"name": "names"}, headers=h)
    assert resp.status_code == 201, resp.text
    repo = (await db_session.execute(
        select(Repository).where(Repository.full_name == "testuser/names")
    )).scalar_one()
    root = _tree(repo.disk_path, [("100644", "blob", _blob(repo.disk_path, b"x\n"), "é.md")])
    _git(repo.disk_path, "update-ref", "refs/heads/main", _git(repo.disk_path, "commit-tree", root, "-m", "n"))

    listed = await client.get(f"{API}/repos/testuser/names/contents/", headers=h)
    assert listed.status_code == 200, listed.text
    assert [entry["name"] for entry in listed.json()] == ["é.md"]
    file = await client.get(f"{API}/repos/testuser/names/contents/é.md", headers=h)
    assert file.status_code == 200, file.text
