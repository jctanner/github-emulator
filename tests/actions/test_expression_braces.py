"""An expression whose body holds a ``}`` is still an expression.

The interpolation pattern refused any ``}`` in the body, so a format string
such as ``format('fullsend-{0}', inputs.agent)`` was never matched. Fullsend's
agent action names its artifact with exactly that fallback, and the run
uploaded it under the literal text ``${{ inputs.artifact-name || ... }}``;
anything that looks artifacts up by ``fullsend-<agent>`` found nothing.

The runner and the server each carry the pattern, so both are covered.
"""

import importlib.util
import random
import re
import time
from pathlib import Path

import pytest

from app.services.workflow_expressions import _EXPRESSION_RE, render_expressions

RUNNER_PATH = Path(__file__).parents[2] / "src" / "runners" / "emulator" / "runner.py"
_SPEC = importlib.util.spec_from_file_location("github_emulator_runner_braces", RUNNER_PATH)
assert _SPEC and _SPEC.loader
runner_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner_module)

ARTIFACT_NAME = "${{ inputs.artifact-name || format('fullsend-{0}', inputs.agent) }}"


@pytest.mark.parametrize(
    ("inputs", "expected"),
    [
        ({"agent": "triage"}, "fullsend-triage"),
        ({"agent": "triage", "artifact-name": ""}, "fullsend-triage"),
        ({"agent": "triage", "artifact-name": "custom"}, "custom"),
    ],
)
def test_runner_renders_the_fullsend_artifact_name(inputs, expected):
    assert runner_module._render_local_action(ARTIFACT_NAME, inputs, {}) == expected


def test_server_renders_a_format_string_with_braces():
    context = {"inputs": {"agent": "review"}}
    assert render_expressions("${{ format('fullsend-{0}', inputs.agent) }}", context) == "fullsend-review"


@pytest.mark.parametrize("pattern", [_EXPRESSION_RE, runner_module._EXPRESSION_RE])
@pytest.mark.parametrize(
    ("text", "bodies"),
    [
        ("${{ a }}-${{ b }}", ["a", "b"]),
        ("${{ format('{0}}', a) }}", ["format('{0}}', a)"]),
        ("${{ 'it''s' }}", ["'it''s'"]),
        ("${{ a }}}", ["a"]),
        ("${{ 'it\\'s' }}", ["'it\\'s'"]),
        ("${{ \"it's\" }}", ["\"it's\""]),
        ("${{ \"a}}b\" }}", ["\"a}}b\""]),
        # An unterminated string falls back to the old rule: the first ``}}``.
        ("${{ 'open }}", ["'open"]),
    ],
)
def test_expression_bodies(pattern, text, bodies):
    assert [match.group(1) for match in pattern.finditer(text)] == bodies


SCANNERS = [_EXPRESSION_RE, runner_module._EXPRESSION_RE]
OLD_PATTERN = re.compile(r"\$\{\{\s*([^}]+?)\s*\}\}")


@pytest.mark.parametrize("scanner", SCANNERS)
@pytest.mark.parametrize(
    "text",
    [
        "${{ " + "'" * 5000 + " x",
        "${{ " + "'a" * 5000 + " x",
        "${{ " + '"' * 5000 + " x",
        "${{ " + "\\'" * 5000 + " x",
        "${{ " + "} " * 5000 + " x",
        "${{ " + " " * 20000,
        "${{ a" + " " * 20000,
        "${{ '" * 20000,
        "${{ \\'" * 20000,
        "${{ a" * 20000,
        "${{ '${{ " * 20000,
        "${{ a } " * 20000,
        "${{ a" * 200000,
        "${{ " + "'\\'" * 100000,
        "${{ '\\${{ " * 50000,
    ],
)
def test_malformed_input_is_scanned_quickly(scanner, text):
    """Runs of quotes or spaces in an unclosed expression once backtracked."""
    started = time.perf_counter()
    assert list(scanner.finditer(text)) == []
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize("scanner", SCANNERS)
def test_an_unclosed_string_falls_back_to_the_old_rule(scanner):
    """The tokenizer reads ``'abc\\'`` as a string ending in a backslash."""
    assert [m.group(1) for m in scanner.finditer("${{ 'abc\\' }}")] == ["'abc\\'"]


def _strings_closed(body: str) -> bool:
    """Whether every string in ``body`` closes under GitHub's rule: a string
    ends at the next matching quote, and a backslash is an ordinary
    character. The emulator's tokenizer also accepts backslash escapes, and
    with backtracking reads some bodies both ways; no single scan agrees
    with it everywhere, so the scanner follows GitHub."""
    index = 0
    while index < len(body):
        if body[index] in "'\"":
            closing = body.find(body[index], index + 1)
            if closing < 0:
                return False
            index = closing + 1
        else:
            index += 1
    return True


@pytest.mark.parametrize("scanner", SCANNERS)
def test_everything_the_old_pattern_matched_still_matches(scanner):
    """A seeded fuzz over the characters that matter: every old match whose
    strings close (GitHub's rule, see ``_strings_closed``) is still found, in
    the same place, with the same body. An old match with an unclosed string
    was never a valid expression."""
    alphabets = [
        (1234, list("${}' \"a\\"), 30),
        # Whole tokens reach the nested and escaped cases characters rarely do.
        (5678, ["${{", "}}", "}", "{", "'", '"', "\\", " ", "a", "$"], 18),
        (9012, ["${{", "}}", "}", "'", "\\", " a "], 14),
    ]
    for seed, alphabet, longest in alphabets:
        rng = random.Random(seed)
        for _ in range(20000):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, longest)))
            old = [(m.group(0), m.group(1).strip()) for m in OLD_PATTERN.finditer(text)]
            new = [(m.group(0), m.group(1)) for m in scanner.finditer(text)]
            assert all(pair in new for pair in old if _strings_closed(pair[1])), text


def test_a_lone_brace_does_not_swallow_the_next_expression():
    """Outside a string the old rule holds: no ``}`` before the closing ``}}``."""
    context = {"inputs": {"a": "VALUE"}}
    assert render_expressions("${{ bad } ${{ inputs.a }}", context) == "${{ bad } VALUE"
    rendered = runner_module._render_local_action("${{ bad } ${{ inputs.a }}", {"a": "VALUE"}, {})
    assert rendered == "${{ bad } VALUE"


@pytest.mark.parametrize("scanner", SCANNERS)
@pytest.mark.parametrize(
    ("text", "bodies"),
    [
        ("${{ ' }}' ${{ inputs.a }}", ["'", "inputs.a"]),
        ("${{ ' } ${{ inputs.a }}' }}", ["inputs.a"]),
        # Matched as the old pattern matched it: the escaped ``$`` still
        # begins a ``${{``, so the old rule decides.
        ("${{ '\\${{ inputs.a }}' }}", ["'\\${{ inputs.a"]),
        # GitHub's documented way to write a literal ``${{``.
        ("${{ '${{' }}", ["'${{'"]),
        # A backslash is an ordinary character, as in GitHub: the string
        # ``'abc\\'`` closes, and so does the expression.
        ("${{ 'abc\\' }} suffix ' }}", ["'abc\\'"]),
    ],
)
def test_a_string_does_not_swallow_the_next_expression(scanner, text, bodies):
    assert [m.group(1) for m in scanner.finditer(text)] == bodies


def test_sub_replaces_only_the_expressions():
    assert _EXPRESSION_RE.sub(lambda m: m.group(1).upper(), "a ${{ x }} b ${{ y }}") == "a X b Y"
    assert runner_module._EXPRESSION_RE.sub(lambda m: "!", "${{}} ${{ }}") == "${{}} !"
