"""``runner.*`` expressions cross the server unrendered.

The server renders a step's ``with:`` at job creation. It has no runner
context, so ``${{ runner.temp }}/fullsend-cache`` came out as
``/fullsend-cache`` and actions/cache/save cached nothing (run 1712). The
runner is the only party that knows its own temp directory, so the server
leaves those expressions for it, the way it already leaves ``steps.*``.

The upstream runner receives its steps through the distributed-task
protocol, where a string is either a literal or a whole expression; text with
an expression inside it has to become the ``format()`` call the upstream
template converter would have produced.
"""

from app.api.actions_runner_protocol import _format_expression, _template_mapping
from app.services.workflow_expressions import render_expressions


CONTEXT = {"github": {"sha": "abc123"}, "steps": {}}


def test_a_plain_runner_path_is_preserved():
    assert render_expressions("${{ runner.temp }}/fullsend-cache", CONTEXT) == (
        "${{ runner.temp }}/fullsend-cache"
    )


def test_a_compound_expression_naming_the_runner_is_preserved():
    text = "${{ runner.os == 'Linux' && 'yes' || 'no' }}"
    assert render_expressions(text, CONTEXT) == text


def test_other_contexts_in_the_same_string_still_render():
    assert render_expressions(
        "${{ runner.temp }}/${{ github.sha }}", CONTEXT
    ) == "${{ runner.temp }}/abc123"


def test_a_key_that_merely_ends_in_runner_is_not_the_context():
    context = {**CONTEXT, "inputs": {"image_runner.name": "x"}, "vars": {"runner": {"x": "1"}}}
    # ``vars.runner.x`` is a path under another root, not the runner context.
    assert render_expressions("${{ vars.runner.x }}", context) == "1"


def test_embedded_expressions_become_a_format_call_for_the_upstream_runner():
    assert _format_expression("${{ runner.temp }}/fullsend-cache") == (
        "format('{0}/fullsend-cache', runner.temp)"
    )
    assert _format_expression("a-${{ steps.mode.outputs.mode }}-${{ job.workflow_sha }}") == (
        "format('a-{0}-{1}', steps.mode.outputs.mode, job.workflow_sha)"
    )


def test_format_call_escapes_literal_braces_and_quotes():
    assert _format_expression("it's {x} ${{ a }}") == "format('it''s {{x}} {0}', a)"


def test_template_mapping_distinguishes_the_three_shapes():
    mapping = _template_mapping({
        "path": "${{ runner.temp }}/c",
        "key": "${{ github.sha }}",
        "plain": "x",
    })
    values = {entry["Key"]: entry["Value"] for entry in mapping["map"]}
    assert values["path"] == {"type": 3, "expr": "format('{0}/c', runner.temp)"}
    assert values["key"] == {"type": 3, "expr": "github.sha"}
    assert values["plain"] == "x"
