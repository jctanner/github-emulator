"""The batched ref resolver agrees with the one-ref-at-a-time resolver."""

import subprocess

from app.git.bare_repo import (
    normalize_branch_ref,
    normalize_branch_refs,
    resolve_ref,
    resolve_refs,
)


def _git(*args, cwd=None):
    env = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "T",
           "GIT_COMMITTER_EMAIL": "t@t", "HOME": "/nonexistent", "PATH": "/usr/bin:/bin"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


async def _repo(tmp_path):
    path = str(tmp_path / "repo.git")
    work = tmp_path / "work"
    _git("init", "-q", "-b", "main", str(work))
    (work / "README.md").write_text("one\n")
    _git("add", ".", cwd=work)
    _git("commit", "-qm", "Initial", cwd=work)
    _git("switch", "-qc", "feature/x", cwd=work)
    (work / "f.txt").write_text("f\n")
    _git("add", ".", cwd=work)
    _git("commit", "-qm", "Feature", cwd=work)
    _git("tag", "v1", "main", cwd=work)
    _git("clone", "-q", "--bare", str(work), path)
    return path, _git("rev-parse", "main", cwd=work), _git("rev-parse", "feature/x", cwd=work)


async def test_resolve_refs_matches_resolve_ref(tmp_path):
    path, main, feature = await _repo(tmp_path)
    names = ["main", "feature/x", "refs/heads/main", "v1", main, main[:12], "nope", "a b"]
    batched = await resolve_refs(path, names)
    for name in names:
        assert batched[name] == await resolve_ref(path, name), name
    assert batched["main"] == main and batched["feature/x"] == feature
    assert batched["nope"] is None


async def test_normalize_branch_refs_matches_single(tmp_path):
    path, _, _ = await _repo(tmp_path)
    refs = ["main", "owner:feature/x", "other:feature/x", "missing", "owner:missing", ""]
    batched = await normalize_branch_refs(path, refs, "owner")
    for ref in refs:
        assert batched[ref] == await normalize_branch_ref(path, ref, "owner"), ref
    assert batched["owner:feature/x"][0] == "feature/x"


async def test_resolve_refs_empty(tmp_path):
    path, _, _ = await _repo(tmp_path)
    assert await resolve_refs(path, []) == {}
    assert await resolve_refs(path, ["", "x\ny"]) == {}
