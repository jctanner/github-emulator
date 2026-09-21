"""Coverage for job-token permission enforcement.

Mirrors GitHub's documented GITHUB_TOKEN semantics: declaring any permission
sets every unspecified scope to none, metadata stays readable, and a refusal
is 403 "Resource not accessible by integration".
"""

import pytest

from app.services.job_permissions import check, normalise_permissions


# The permission blocks the Fullsend dispatch actually declares.
ROUTE = {"contents": "read", "issues": "read", "pull-requests": "read"}
TRIAGE = {"actions": "write", "contents": "read", "id-token": "write", "issues": "write"}
CODE = {
    "actions": "write",
    "contents": "write",
    "id-token": "write",
    "issues": "write",
    "packages": "read",
    "pull-requests": "write",
}


def test_declaring_any_permission_denies_everything_unspecified():
    """The rule that makes this worth enforcing at all."""
    # Triage never mentions pull-requests, so it has no pull-request access,
    # not even read.
    assert check("GET", "/api/v3/repos/o/r/pulls/1", TRIAGE) is not None
    assert check("POST", "/api/v3/repos/o/r/pulls", TRIAGE) is not None
    # Code declares it, so it may write.
    assert check("POST", "/api/v3/repos/o/r/pulls", CODE) is None


def test_read_grants_do_not_permit_writes():
    assert check("GET", "/api/v3/repos/o/r/contents/f", TRIAGE) is None
    assert check("PUT", "/api/v3/repos/o/r/contents/f", TRIAGE) is not None
    assert check("PUT", "/api/v3/repos/o/r/contents/f", CODE) is None


def test_triage_may_comment_but_route_may_not():
    """The asymmetry the conformance plan cares about."""
    assert check("POST", "/api/v3/repos/o/r/issues/1/comments", TRIAGE) is None
    assert check("POST", "/api/v3/repos/o/r/issues/1/comments", ROUTE) is not None


def test_metadata_is_always_readable():
    assert check("GET", "/api/v3/repos/o/r", {}) is None
    # Reading a collaborator's permission is metadata-level: the dispatch
    # authorizes events with exactly this call while declaring no
    # administration scope.
    assert check("GET", "/api/v3/repos/o/r/collaborators/u/permission", ROUTE) is None
    # Managing collaborators is not.
    assert check("PUT", "/api/v3/repos/o/r/collaborators/u", ROUTE) is not None


def test_empty_block_grants_nothing_but_metadata():
    assert check("POST", "/api/v3/repos/o/r/issues", {}) is not None
    assert check("GET", "/api/v3/repos/o/r", {}) is None


def test_absent_block_is_permissive():
    """Documented deviation: GitHub defers to repo settings, which we lack."""
    assert check("POST", "/api/v3/repos/o/r/issues", None) is None


def test_read_all_and_write_all_shorthands():
    assert check("GET", "/api/v3/repos/o/r/issues", "read-all") is None
    assert check("POST", "/api/v3/repos/o/r/issues", "read-all") is not None
    assert check("POST", "/api/v3/repos/o/r/issues", "write-all") is None


def test_unmapped_paths_allow_reads_and_deny_writes():
    """Documented deviation, so a gap in the map is loud rather than silent."""
    assert check("GET", "/api/v3/repos/o/r/some-future-thing", TRIAGE) is None
    assert check("POST", "/api/v3/repos/o/r/some-future-thing", TRIAGE) is not None


def test_normalise_permissions_distinguishes_absent_from_empty():
    assert normalise_permissions(None) is None
    assert normalise_permissions({}) == {}


@pytest.mark.parametrize("path", [
    "/api/v3/repos/o/r/issues/1/comments",
    "repos/o/r/issues/1/comments",
    "/repos/o/r/issues/1/comments",
])
def test_path_prefixes_are_handled(path):
    assert check("POST", path, TRIAGE) is None


def test_workflow_level_permissions_are_inherited_by_jobs():
    """A workflow that scopes its token once at the top scopes every job."""
    from app.services.workflow_service import build_job_graph

    graph = build_job_graph({
        "permissions": {"contents": "read", "id-token": "write"},
        "jobs": {"trust": {"runs-on": "ubuntu-latest", "steps": []}},
    })
    assert graph[0]["permissions"] == {"contents": "read", "id-token": "write"}


def test_a_jobs_own_block_replaces_the_workflow_block_rather_than_merging():
    """GitHub replaces; merging would silently widen a job's grant."""
    from app.services.workflow_service import build_job_graph

    graph = build_job_graph({
        "permissions": {"contents": "write", "id-token": "write"},
        "jobs": {
            "narrow": {
                "runs-on": "ubuntu-latest",
                "permissions": {"issues": "read"},
                "steps": [],
            }
        },
    })
    assert graph[0]["permissions"] == {"issues": "read"}


def test_no_block_anywhere_is_unchanged():
    """Behaviour for a workflow that declares nothing is left alone."""
    from app.services.workflow_service import build_job_graph

    graph = build_job_graph({
        "jobs": {"plain": {"runs-on": "ubuntu-latest", "steps": []}},
    })
    assert graph[0]["permissions"] == {}
