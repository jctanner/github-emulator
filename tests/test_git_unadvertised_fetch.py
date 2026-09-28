"""Which commits the git transport will serve, and why that is already right.

A run once failed with "Server does not allow request for unadvertised object"
while checking out its own head commit. The obvious reading was that the
transport was too strict, since Actions routinely checks out a commit that is
not a branch tip, and the obvious fix was to set
``uploadpack.allowAnySHA1InWant``. That reading was wrong, and these tests are
here so nobody reaches that conclusion again.

Measured against real git:

- A commit **reachable** from some ref is served by upload-pack when asked
  for, with no configuration at all. Not being a branch tip is not the problem
  on the server side.
- A commit reachable from **nothing** is refused, and GitHub refuses it too.

So that failure was never about the transport. It was a run referencing a
commit that no ref pointed at, which is fixed where the run is created.
Enabling ``allowAnySHA1InWant`` would have made this emulator *more*
permissive than the thing it emulates, and hidden the real defect.

One thing the first measurement missed, found by a later run (1766): a real
git **client** will not ask for an unadvertised object unless the server
advertises ``allow-reachable-sha1-in-want``, and upload-pack only advertises
it with ``uploadpack.allowReachableSHA1InWant``. A push that landed between a
run's creation and its checkout left the run's own commit reachable but no
longer a tip, and ``git fetch`` refused client-side with "Server does not
allow request for unadvertised object". The transport now sets that one
option, reachable only, which is the case Actions needs and the case GitHub
serves; the unreachable refusal below still holds.

If a future case genuinely needs an unreachable commit to be fetchable, the
faithful answer is to give it a ref the way GitHub does with
``refs/pull/N/head``, not to loosen the server.
"""

import subprocess

import pytest


GIT_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin",
    "HOME": "/nonexistent",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@localhost",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@localhost",
}


def _git(*args, cwd=None, check=True):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        check=check, env=GIT_ENV,
    )


def _pkt(line: bytes) -> bytes:
    return f"{len(line) + 4:04x}".encode() + line


def _want(repo, sha: str):
    """Ask upload-pack for one commit, the way the smart HTTP transport does."""
    request = _pkt(b"want " + sha.encode() + b"\n") + b"0000" + _pkt(b"done\n")
    return subprocess.run(
        ["git-upload-pack", "--stateless-rpc", str(repo)],
        input=request, capture_output=True, env=GIT_ENV,
    )


@pytest.fixture
def origin(tmp_path):
    """A bare repo with a superseded commit and an unreferenced one."""
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-q", "-b", "main", ".", cwd=work)
    (work / "first.txt").write_text("one\n")
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "first", cwd=work)
    superseded = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    (work / "second.txt").write_text("two\n")
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", "second", cwd=work)

    bare = tmp_path / "origin.git"
    _git("clone", "-q", "--bare", str(work), str(bare))

    tip = _git("rev-parse", "HEAD", cwd=bare).stdout.strip()
    tree = _git("rev-parse", "HEAD^{tree}", cwd=bare).stdout.strip()
    unreferenced = _git(
        "commit-tree", "-m", "no ref points here", "-p", tip, tree, cwd=bare,
    ).stdout.strip()
    return bare, superseded, unreferenced


def test_a_commit_that_is_not_a_branch_tip_is_served(origin):
    """The case Actions actually needs, and it needs no configuration."""
    bare, superseded, _unreferenced = origin
    result = _want(bare, superseded)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout.startswith(b"0008NAK\nPACK")


def test_a_commit_no_ref_reaches_is_refused(origin):
    """GitHub refuses this too, so the emulator matching it is correct."""
    bare, _superseded, unreferenced = origin
    result = _want(bare, unreferenced)
    assert result.returncode != 0
    assert b"not our ref" in result.stdout + result.stderr


def test_the_transport_does_not_loosen_upload_pack(origin):
    """Guard against re-introducing the permissive shortcut."""
    from app.git import smart_http, ssh_server

    for module in (smart_http, ssh_server):
        source = __import__("inspect").getsource(module)
        assert "allowAnySHA1InWant" not in source, (
            f"{module.__name__} enables allowAnySHA1InWant, which makes this "
            "emulator more permissive than GitHub; see this module's docstring"
        )


def test_the_advertisement_offers_reachable_sha1_in_want(origin):
    """What a real client checks before it will ask for a non-tip commit."""
    bare, _superseded, _unreferenced = origin
    without = subprocess.run(
        ["git-upload-pack", "--stateless-rpc", "--advertise-refs", str(bare)],
        capture_output=True, env=GIT_ENV,
    ).stdout
    assert b"allow-reachable-sha1-in-want" not in without
    with_option = subprocess.run(
        ["git", "-c", "uploadpack.allowReachableSHA1InWant=true",
         "upload-pack", "--stateless-rpc", "--advertise-refs", str(bare)],
        capture_output=True, env=GIT_ENV,
    ).stdout
    assert b"allow-reachable-sha1-in-want" in with_option
    assert b"allow-any-sha1-in-want" not in with_option


def test_a_real_client_can_fetch_a_superseded_commit_over_the_transport(origin, tmp_path):
    """git fetch <sha> of a reachable non-tip commit, through upload-pack
    invoked the way smart_http invokes it."""
    bare, superseded, unreferenced = origin
    clone = tmp_path / "clone"
    _git("init", "-q", str(clone))
    _git("remote", "add", "origin", str(bare), cwd=clone)
    # Protocol v0, which is what the emulator's smart HTTP speaks. (Over v2
    # the advertisement is a separate request and git accepts any object
    # in a want, so v2 would not exercise the capability at all.)
    server = "remote.origin.uploadpack=git -c uploadpack.allowReachableSHA1InWant=true upload-pack"
    plain = _git("-c", "protocol.version=0", "fetch", "origin", superseded, cwd=clone, check=False)
    assert plain.returncode != 0 and "unadvertised" in plain.stderr, "without the option a client refuses to ask"
    served = _git("-c", "protocol.version=0", "-c", server,
                  "fetch", "origin", superseded, cwd=clone, check=False)
    assert served.returncode == 0, served.stderr
    refused = _git("-c", "protocol.version=0", "-c", server,
                   "fetch", "origin", unreferenced, cwd=clone, check=False)
    assert refused.returncode != 0
