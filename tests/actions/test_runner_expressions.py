"""Expression rendering inside local composite actions.

The renderer understood three shapes and returned everything else as its own
text, so a fallback chain in a composite step's ``env:`` reached the shell as
the literal characters ``${{ ... }}``. That is the same defect the server-side
renderer had; the fix is the same, routing anything with an operator through
the parser the runner already carries.
"""

import importlib.util
from pathlib import Path

import pytest


RUNNER_PATH = (
    Path(__file__).parents[2] / "src" / "runners" / "emulator" / "runner.py"
)
_SPEC = importlib.util.spec_from_file_location("github_emulator_runner", RUNNER_PATH)
assert _SPEC and _SPEC.loader
runner_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner_module)

render = runner_module._render_local_action


def test_fallback_chain_returns_the_first_non_empty_value():
    """The expression that reached git as an invalid refspec."""
    outputs = {"detect": {"source-ref": "de965fc4", "version-url": "vde965fc4"}}
    rendered = render(
        "${{ steps.detect.outputs.source-ref || steps.detect.outputs.version-url }}",
        {},
        outputs,
    )
    assert rendered == "de965fc4"


def test_fallback_chain_falls_through_when_the_first_is_empty():
    outputs = {"detect": {"source-ref": "", "version-url": "vde965fc4"}}
    rendered = render(
        "${{ steps.detect.outputs.source-ref || steps.detect.outputs.version-url }}",
        {},
        outputs,
    )
    assert rendered == "vde965fc4"


def test_a_compound_expression_never_renders_as_its_own_text():
    """The failure mode: literal ${{ }} reaching a shell."""
    rendered = render(
        "${{ steps.absent.outputs.a || steps.absent.outputs.b }}", {}, {}
    )
    assert "${{" not in rendered
    assert rendered == ""


def test_hyphenated_output_names_resolve():
    outputs = {"detect": {"install-method": "source"}}
    assert render("${{ steps.detect.outputs.install-method }}", {}, outputs) == "source"
    assert render(
        "${{ steps.detect.outputs.install-method == 'source' }}", {}, outputs
    ) == "true"


def test_comparisons_render_as_actions_spells_them():
    outputs = {"detect": {"install-method": "source"}}
    assert render(
        "${{ steps.detect.outputs.install-method == 'vendored' }}", {}, outputs
    ) == ""
    assert render(
        "${{ steps.detect.outputs.install-method != 'vendored' }}", {}, outputs
    ) == "true"


def test_inputs_participate_in_compound_expressions():
    rendered = render(
        "${{ inputs.fullsend-dir || 'default' }}", {"fullsend-dir": ""}, {}
    )
    assert rendered == "default"
    rendered = render(
        "${{ inputs.fullsend-dir || 'default' }}", {"fullsend-dir": ".fullsend"}, {}
    )
    assert rendered == ".fullsend"


def test_the_job_token_can_be_a_fallback_without_being_logged_as_a_path():
    rendered = render(
        "${{ inputs.token || github.token }}", {"token": ""}, {}, "job-scoped-token"
    )
    assert rendered == "job-scoped-token"


def test_plain_paths_still_take_the_cheap_route():
    outputs = {"detect": {"source-ref": "de965fc4"}}
    assert render("${{ steps.detect.outputs.source-ref }}", {}, outputs) == "de965fc4"
    assert render("${{ inputs.role }}", {"role": "triage"}, {}) == "triage"
    assert render("${{ github.token }}", {}, {}, "tok") == "tok"


def test_an_unsupported_context_stays_visible_rather_than_rendering_empty():
    """A quiet empty string would hide a renderer that did not run."""
    rendered = render("${{ vars.SOMETHING == 'Linux' }}", {}, {})
    assert rendered == "${{ vars.SOMETHING == 'Linux' }}"


def test_the_runner_context_resolves_because_only_the_runner_knows_it():
    """The server leaves runner.* alone, so a composite action arrives with it."""
    rendered = render("${{ runner.temp }}/fullsend-src/go.mod", {}, {})
    assert rendered.endswith("/fullsend-src/go.mod")
    assert "${{" not in rendered
    assert rendered.startswith(runner_module.RUNNER_TEMP)


def test_runner_context_works_inside_a_compound_expression():
    assert render("${{ runner.os == 'Linux' }}", {}, {}) in ("true", "")
    assert render("${{ runner.arch }}", {}, {}) in ("X64", "ARM64")


def test_an_unsupported_single_path_is_also_left_alone():
    rendered = render("${{ matrix.target }}", {}, {})
    assert rendered == "${{ matrix.target }}"


def test_rendering_recurses_through_a_step_mapping():
    step = {
        "name": "Clone",
        "env": {
            "SOURCE_REF": (
                "${{ steps.detect.outputs.source-ref "
                "|| steps.detect.outputs.version-url }}"
            ),
        },
    }
    rendered = render(step, {}, {"detect": {"version-url": "v1.2.3"}})
    assert rendered["env"]["SOURCE_REF"] == "v1.2.3"


def test_step_conditions_still_work_through_the_shared_context():
    outputs = {"route": {"stage": "triage"}}
    assert runner_module._evaluate_step_if(
        "steps.route.outputs.stage == 'triage' && always()", outputs
    )
    assert not runner_module._evaluate_step_if(
        "steps.route.outputs.stage == 'code'", outputs
    )


def test_a_condition_on_an_unsupported_context_still_fails_loudly():
    with pytest.raises(runner_module.StepConditionError):
        runner_module._evaluate_step_if("vars.SOMETHING == 'Linux'", {})


def test_format_builds_a_string_from_positional_arguments():
    """The Actions vocabulary the agent action uses to locate a target repo."""
    assert runner_module._format_expression("{0}/go.mod", ["target-repo"]) == (
        "target-repo/go.mod"
    )
    assert runner_module._format_expression("{1}-{0}", ["a", "b"]) == "b-a"


def test_format_escapes_doubled_braces():
    assert runner_module._format_expression("{{0}}", ["x"]) == "{0}"
    assert runner_module._format_expression("a {{b}} {0}", ["c"]) == "a {b} c"


def test_format_is_not_str_format():
    """str.format would reach attributes and indexes; Actions does not."""
    import pytest

    with pytest.raises(ValueError):
        runner_module._format_expression("{0.__class__}", ["x"])
    with pytest.raises(ValueError):
        runner_module._format_expression("{0}", [])


def test_a_condition_using_format_is_evaluated_not_refused():
    """The condition that failed the job with an empty log."""
    assert runner_module._evaluate_step_if(
        "format('{0}/go.mod', 'target-repo') == 'target-repo/go.mod'", {}
    )
    assert not runner_module._evaluate_step_if(
        "format('{0}/go.mod', 'target-repo') == 'other/go.mod'", {}
    )


def test_a_composite_condition_error_reaches_the_log(monkeypatch, tmp_path):
    """A failure with no message is the pattern this project keeps removing."""
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))
    action = Path(str(tmp_path)) / "act"
    action.mkdir(parents=True)
    (action / "action.yml").write_text(
        "name: Demo\n"
        "runs:\n"
        "  using: composite\n"
        "  steps:\n"
        "    - shell: bash\n"
        "      if: totallyUnsupported('x')\n"
        "      run: echo unreachable\n"
    )
    client = runner_module.RunnerClient.__new__(runner_module.RunnerClient)
    client._masks = set()
    chunks: list[str] = []
    result, output, _updates = client._composite_step(
        {"uses": "./act"}, {}, {}, log_callback=chunks.append,
    )
    assert result == "failure"
    assert "totallyUnsupported" in "".join(chunks)
    assert "totallyUnsupported" in output
