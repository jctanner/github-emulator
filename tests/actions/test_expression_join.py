"""``join()`` in workflow expressions.

Fullsend's dispatch passes labels to its routing as
``join(github.event.pull_request.labels.*.name, ',')``. The emulator had no
``join``, so the expression rendered empty, the ``fullsend-fix`` label was
never seen, and a human's pull request never routed to the fix stage.
"""

from app.services.workflow_service import render_expressions

EVENT = {
    "pull_request": {"labels": [{"name": "fullsend-fix"}, {"name": "bug"}]},
    "issue": {"labels": []},
}
CONTEXT = {"github": {"event": EVENT}}


def test_join_reads_an_object_filter_with_a_separator():
    assert render_expressions(
        "${{ join(github.event.pull_request.labels.*.name, ',') }}", CONTEXT,
    ) == "fullsend-fix,bug"


def test_join_defaults_to_a_comma():
    assert render_expressions(
        "${{ join(github.event.pull_request.labels.*.name) }}", CONTEXT,
    ) == "fullsend-fix,bug"


def test_join_of_an_empty_array_is_empty():
    assert render_expressions(
        "${{ join(github.event.issue.labels.*.name, ',') }}", CONTEXT,
    ) == ""


def test_join_of_a_string_is_that_string():
    assert render_expressions("${{ join('solo', '-') }}", CONTEXT) == "solo"


def test_join_converts_items_to_strings():
    assert render_expressions(
        "${{ join(fromJSON('[1, true, \"x\"]'), ' ') }}", CONTEXT,
    ) == "1 true x"
