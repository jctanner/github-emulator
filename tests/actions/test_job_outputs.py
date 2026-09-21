"""Regression coverage for job outputs, the needs context, and fromJSON.

A reusable dispatch workflow routes by writing a stage name to a job output and
having each stage job gate on ``needs.<job>.outputs.stage``. That requires job
outputs to be resolved from step outputs, exposed through a ``needs`` context
keyed by YAML job key, and readable through ``fromJSON`` for structured
payloads.
"""

import pytest

from app.services.workflow_expressions import evaluate_job_if, render_expressions
from app.services.workflow_service import (
    build_job_graph,
    build_needs_context,
    resolve_job_outputs,
)


def test_build_job_graph_parses_outputs():
    graph = build_job_graph({
        "jobs": {
            "route": {
                "runs-on": "ubuntu-latest",
                "outputs": {"stage": "${{ steps.route.outputs.stage }}"},
                "steps": [{"id": "route", "run": "echo"}],
            }
        }
    })
    assert graph[0]["outputs"] == {"stage": "${{ steps.route.outputs.stage }}"}


def test_resolve_job_outputs_from_step_outputs():
    resolved = resolve_job_outputs(
        {
            "stage": "${{ steps.route.outputs.stage }}",
            "payload": "${{ steps.payload.outputs.event_payload }}",
            "missing": "${{ steps.absent.outputs.nothing }}",
        },
        {
            "route": {"stage": "triage"},
            "payload": {"event_payload": '{"issue":{"number":38}}'},
        },
    )
    assert resolved["stage"] == "triage"
    assert resolved["payload"] == '{"issue":{"number":38}}'
    assert resolved["missing"] == ""


def test_resolve_job_outputs_without_config_is_empty():
    assert resolve_job_outputs({}, {"a": {"b": "c"}}) == {}


def test_needs_context_is_keyed_by_job_key_not_display_name():
    """``needs:`` references the YAML key, while ``name:`` is a display label."""

    class _Job:
        def __init__(self, job_key, name, outputs, conclusion):
            self.job_key = job_key
            self.name = name
            self.outputs = outputs
            self.conclusion = conclusion

    context = build_needs_context([
        _Job("dispatch / route", "Route", {"stage": "triage"}, "success"),
    ])
    assert context["dispatch / route"]["outputs"]["stage"] == "triage"
    assert context["dispatch / route"]["result"] == "success"


def test_stage_job_condition_reads_needs_outputs():
    context = {"needs": {"route": {"outputs": {"stage": "triage"}, "result": "success"}}}
    assert evaluate_job_if("needs.route.outputs.stage == 'triage'", context)
    assert not evaluate_job_if("needs.route.outputs.stage == 'code'", context)


def test_from_json_reads_structured_job_output():
    context = {
        "needs": {
            "route": {
                "outputs": {
                    "event_payload": '{"issue":{"number":38,"html_url":"https://x/38"}}'
                }
            }
        }
    }
    rendered = render_expressions(
        "${{ fromJSON(needs.route.outputs.event_payload).issue.html_url }}", context
    )
    assert rendered == "https://x/38"
    assert (
        render_expressions(
            "${{ fromJSON(needs.route.outputs.event_payload).issue.number }}", context
        )
        == "38"
    )


def test_from_json_supports_index_access_and_absent_values():
    context = {"inputs": {"matrix": '{"include":[{"agent":"triage"}]}', "blank": ""}}
    assert (
        render_expressions("${{ fromJSON(inputs.matrix).include[0].agent }}", context)
        == "triage"
    )
    assert evaluate_job_if("fromJSON(inputs.matrix).include[0] != null", context)
    assert not evaluate_job_if("fromJSON(inputs.blank).include[0] != null", context)


def test_plain_context_paths_and_fallbacks_are_unchanged():
    context = {
        "github": {"event": {"action": "opened"}, "event_name": "issues"},
        "inputs": {},
    }
    assert render_expressions("${{ github.event.action }}", context) == "opened"
    assert (
        render_expressions("${{ inputs.missing || github.event_name }}", context)
        == "issues"
    )


def test_step_expressions_are_preserved_for_the_runner():
    """The runner resolves step outputs at execution time, so they stay intact."""
    rendered = render_expressions("${{ steps.build.outputs.sha }}", {})
    assert rendered == "${{ steps.build.outputs.sha }}"


def test_reusable_call_preserves_compound_expressions():
    """Only bare paths may be substituted lexically into a called workflow.

    ``${{ inputs.matrix == '' }}`` merely starts with "inputs."; rewriting it
    as a path lookup produced an empty string that then failed to parse, which
    silently skipped the job guarding the whole dispatch.
    """
    from app.services.workflow_service import _render_reusable_call_context

    inputs = {"matrix": "", "install_mode": "per-repo"}
    assert (
        _render_reusable_call_context("${{ inputs.install_mode }}", inputs, {})
        == "per-repo"
    )
    assert (
        _render_reusable_call_context("${{ inputs.matrix == '' }}", inputs, {})
        == "${{ inputs.matrix == '' }}"
    )


def test_combined_caller_and_child_conditions_are_parseable():
    """A caller's ``if:`` AND-ed into a child must not nest ``${{ }}`` markers."""
    from app.services.workflow_service import _combine_conditions

    combined = _combine_conditions(
        "github.event_name != 'issue_comment'", "${{ inputs.matrix == '' }}"
    )
    assert "${{" not in combined
    context = {"github": {"event_name": "issues"}, "inputs": {"matrix": ""}}
    assert evaluate_job_if(combined, context)

    stage = _combine_conditions(
        "github.event_name != 'issue_comment'", "needs.route.outputs.stage == 'triage'"
    )
    assert evaluate_job_if(
        stage, {**context, "needs": {"route": {"outputs": {"stage": "triage"}}}}
    )
    assert not evaluate_job_if(
        stage, {**context, "needs": {"route": {"outputs": {"stage": "code"}}}}
    )


def test_combine_conditions_handles_missing_sides():
    from app.services.workflow_service import _combine_conditions

    assert _combine_conditions("a == 'b'", None) == "a == 'b'"
    assert _combine_conditions("${{ a == 'b' }}", None) == "a == 'b'"


def test_dynamic_matrix_does_not_crash_expansion():
    """An unrendered ``matrix: ${{ fromJSON(...) }}`` must not raise.

    It previously surfaced as a 500 on the API call that triggered the event.
    """
    from app.services.workflow_service import expand_matrix

    job = {"key": "harness-run", "strategy": {"matrix": "${{ fromJSON(inputs.matrix) }}"}}
    expanded = expand_matrix(job)
    assert len(expanded) == 1
    assert expanded[0] is job


def test_reusable_call_inputs_render_against_the_caller_context():
    """``with:`` values are written in the caller's terms and must be rendered."""
    from app.services.workflow_service import _job_context

    base = {
        "github": {"event": {"action": "opened"}, "event_name": "issues"},
        "inputs": {},
        "vars": {},
        "secrets": {},
    }
    job = {"_call_inputs": {"event_action": "${{ github.event.action }}"}}
    assert _job_context(base, job)["inputs"]["event_action"] == "opened"


def test_step_conditions_are_resolved_server_side_unless_runtime():
    """The runner only knows ``steps.*``; everything else must be decided here.

    A guard such as ``inputs.event_action == ''`` evaluated to true on the
    runner regardless of the real value, firing the workflow's own validation.
    """
    from app.services.workflow_service import _resolve_step_condition

    context = {"inputs": {"event_action": "opened"}, "github": {"event_name": "issues"}}
    assert _resolve_step_condition("${{ inputs.event_action == '' }}", context) == "false"
    assert _resolve_step_condition("${{ inputs.event_action != '' }}", context) == "true"
    # Runtime-dependent conditions keep their runtime parts for the runner.
    for preserved, marker in (
        ("${{ steps.route.outputs.stage == 'triage' }}", "steps.route.outputs.stage"),
        ("${{ always() }}", "always()"),
        ("${{ success() }}", "success()"),
    ):
        assert marker in _resolve_step_condition(preserved, context)


def test_server_preserves_github_token_for_the_runner():
    """The server must not bake the credential into stored step records."""
    assert (
        render_expressions("${{ github.token }}", {"github": {"token": "secret"}})
        == "${{ github.token }}"
    )


def test_runner_resolves_github_token_from_the_job_token():
    """The runner substitutes the scoped credential issued with the job.

    Left unresolved, the literal expression reached `gh` as a bearer token and
    the routing step's collaborator permission check failed closed, so no stage
    was ever selected.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "runner_for_token_test", "src/runners/emulator/runner.py"
    )
    runner_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner_module)

    step = {"env": {"GH_TOKEN": "${{ github.token }}", "KEEP": "${{ steps.a.outputs.b }}"}}
    rendered = runner_module._render_local_action(
        step, {}, {"a": {"b": "value"}}, "job-token-123"
    )
    assert rendered["env"]["GH_TOKEN"] == "job-token-123"
    assert rendered["env"]["KEEP"] == "value"
    # Without a token the expression resolves to empty rather than leaking the
    # literal into the environment.
    assert runner_module._render_local_action("${{ github.token }}", {}, {}) == ""


def test_job_token_authenticates_and_carries_job_permissions():
    from app.services.job_token_service import issue_job_token

    class _Job:
        id = 7
        run_id = 3

    token = issue_job_token(_Job())
    assert token.count(".") == 2


def test_runner_points_the_gh_cli_at_the_emulator(monkeypatch):
    """gh ignores GITHUB_API_URL; without GH_HOST it calls api.github.com.

    That produced "Bad credentials" on every call regardless of the job
    token's validity, which looked like an authentication problem but was a
    routing one.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "runner_for_host_test", "src/runners/emulator/runner.py"
    )
    runner_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner_module)

    monkeypatch.setattr(runner_module, "EMULATOR_URL", "https://github.local")
    assert runner_module._emulator_host() == "github.local"
    monkeypatch.setattr(runner_module, "EMULATOR_URL", "https://ghemu.example:8443")
    assert runner_module._emulator_host() == "ghemu.example"


def test_workflow_token_is_mirrored_for_the_gh_enterprise_variable():
    """gh reads GH_ENTERPRISE_TOKEN for any host other than github.com.

    Setting only GH_TOKEN left gh unauthenticated against the emulator, which
    it reports as "Requires authentication" rather than a credential error.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "runner_for_env_test", "src/runners/emulator/runner.py"
    )
    runner_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner_module)

    client = runner_module.RunnerClient()
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs.get("env") or {})
        raise RuntimeError("stop after env assembly")

    import subprocess

    original = subprocess.Popen
    subprocess.Popen = fake_run
    try:
        client._run_step(
            {"number": 1, "name": "s", "run": "true", "env": {"GH_TOKEN": "tok-1"}},
            {"job_id": 1, "repository": "o/r"},
            {},
        )
    except Exception:
        pass
    finally:
        subprocess.Popen = original

    assert captured.get("GH_ENTERPRISE_TOKEN") == "tok-1"
    assert captured.get("GITHUB_ENTERPRISE_TOKEN") == "tok-1"
    assert captured.get("GH_TOKEN") == "tok-1"


def test_job_outputs_resolve_compound_step_expressions():
    """A job's ``outputs:`` routinely combines several step outputs.

    The dispatch's routing output is
    ``a != 'true' && b != 'true' && steps.route.outputs.stage || ''``. The
    path-lookup fallback could not evaluate that and returned empty, silently
    erasing the routing decision after the router had already chosen a stage.
    """
    config = {
        "stage": (
            "${{ steps.role-check.outputs.skipped != 'true' "
            "&& steps.agent-check.outputs.skipped != 'true' "
            "&& steps.route.outputs.stage || '' }}"
        )
    }
    chosen = {"route": {"stage": "triage"}, "role-check": {}, "agent-check": {}}
    assert resolve_job_outputs(config, chosen)["stage"] == "triage"

    # A gate that skipped must blank the stage rather than leak it through.
    gated = {**chosen, "role-check": {"skipped": "true"}}
    assert resolve_job_outputs(config, gated)["stage"] == ""


def test_literal_fallbacks_and_plain_paths_both_work():
    context = {"github": {"event_name": "issues"}, "inputs": {}}
    assert render_expressions("${{ inputs.missing || 'fallback' }}", context) == "fallback"
    assert render_expressions("${{ github.event_name }}", context) == "issues"
    assert (
        render_expressions("${{ inputs.missing || github.event_name }}", context)
        == "issues"
    )


def test_needs_context_answers_to_both_prefixed_and_original_job_keys():
    """Inlining renames jobs, but the called workflow's expressions do not.

    ``reusable-dispatch.yml`` says ``needs.route.outputs.stage`` while the
    inlined job is keyed ``dispatch / route``. Registering only the prefixed
    key made every stage condition read an empty stage and skip.
    """

    class _Job:
        def __init__(self, job_key, outputs, conclusion="success"):
            self.job_key = job_key
            self.name = job_key
            self.outputs = outputs
            self.conclusion = conclusion

    context = build_needs_context([_Job("dispatch / route", {"stage": "triage"})])
    assert context["dispatch / route"]["outputs"]["stage"] == "triage"
    assert context["route"]["outputs"]["stage"] == "triage"
    assert evaluate_job_if(
        "needs.route.outputs.stage == 'triage'", {"needs": context}
    )


def test_prefixed_key_is_not_clobbered_by_a_bare_alias():
    class _Job:
        def __init__(self, job_key, outputs):
            self.job_key = job_key
            self.name = job_key
            self.outputs = outputs
            self.conclusion = "success"

    context = build_needs_context([
        _Job("route", {"stage": "explicit"}),
        _Job("dispatch / route", {"stage": "inlined"}),
    ])
    assert context["route"]["outputs"]["stage"] == "explicit"
    assert context["dispatch / route"]["outputs"]["stage"] == "inlined"


# --- wave 2 -----------------------------------------------------------------

def test_to_json_serialises_a_context():
    context = {"vars": {"B": "2", "A": "1"}}
    assert render_expressions("${{ toJSON(vars) }}", context) == '{"A":"1","B":"2"}'
    assert render_expressions("${{ fromJSON(toJSON(vars)).A }}", context) == "1"


def test_hash_files_conditions_are_left_for_the_runner():
    """hashFiles reads the job workspace, which the server cannot see."""
    from app.services.workflow_service import _resolve_step_condition

    condition = "${{ hashFiles('.defaults/action.yml') == '' }}"
    resolved = _resolve_step_condition(condition, {})
    assert "hashFiles('.defaults/action.yml')" in resolved
    assert resolved not in ("true", "false"), "must not be decided on the server"


def test_undecidable_conditions_are_deferred_rather_than_defaulted():
    """Resolving an unevaluable condition to false silently drops the step."""
    from app.services.workflow_service import _resolve_step_condition

    condition = "${{ mysteryFunction(1) == 'x' }}"
    resolved = _resolve_step_condition(condition, {})
    assert "mysteryFunction" in resolved
    assert resolved not in ("true", "false"), "must not be decided on the server"


def test_job_context_reports_the_workflow_source():
    from app.services.workflow_service import _job_context

    base = {"github": {"repository": "owner/target", "sha": "deadbeef"}}
    inlined = _job_context(
        base, {"_workflow_repository": "fullsend-ai/fullsend", "_workflow_sha": "abc123"}
    )
    assert inlined["job"]["workflow_repository"] == "fullsend-ai/fullsend"
    assert inlined["job"]["workflow_sha"] == "abc123"

    # A job defined in this repository reports its own repository and commit.
    local = _job_context(base, {})
    assert local["job"]["workflow_repository"] == "owner/target"
    assert local["job"]["workflow_sha"] == "deadbeef"


def test_runner_hash_files_matches_only_existing_files(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "runner_for_hashfiles", "src/runners/emulator/runner.py"
    )
    runner_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner_module)
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path))

    # Absent files hash to the empty string, which is how a workflow asks
    # "are these missing?".
    assert runner_module._hash_files([".defaults/action.yml"]) == ""

    target = tmp_path / ".defaults"
    target.mkdir()
    (target / "action.yml").write_text("name: x")
    digest = runner_module._hash_files([".defaults/action.yml"])
    assert digest and digest != ""


def test_runner_fails_loudly_on_an_unevaluable_step_condition():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "runner_for_condition", "src/runners/emulator/runner.py"
    )
    runner_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner_module)

    with pytest.raises(runner_module.StepConditionError):
        runner_module._evaluate_step_if("${{ mysteryFunction(1) }}", {})
    # A condition it understands still evaluates normally.
    assert runner_module._evaluate_step_if("${{ always() }}", {}) is True


def test_mixed_step_conditions_bind_server_context_before_deferring():
    """A condition may gate on both a step output and the event context.

    Deferring it whole left the runner unable to resolve the `github.*` half.
    Once unevaluable conditions started failing loudly, that turned a step
    which should simply skip into a failed job.
    """
    from app.services.workflow_service import _resolve_step_condition

    condition = (
        "steps.route.outputs.stage != '' "
        "&& github.event_name == 'issue_comment' "
        "&& github.event.issue.pull_request"
    )
    issues = {"github": {"event_name": "issues", "event": {"issue": {}}}}
    bound = _resolve_step_condition(condition, issues)
    assert "steps.route.outputs.stage" in bound, "step output must survive"
    assert "github." not in bound, "server context must be bound"
    assert "'issues' == 'issue_comment'" in bound

    comment = {
        "github": {
            "event_name": "issue_comment",
            "event": {"issue": {"pull_request": {"url": "u"}}},
        }
    }
    bound_comment = _resolve_step_condition(condition, comment)
    assert "'issue_comment' == 'issue_comment'" in bound_comment
    assert bound_comment.rstrip().endswith("true")


def test_bound_conditions_are_evaluable_by_the_runner():
    from app.services.workflow_service import _resolve_step_condition
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "runner_for_bound", "src/runners/emulator/runner.py"
    )
    runner_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner_module)

    condition = "steps.route.outputs.stage != '' && github.event_name == 'issue_comment'"
    bound = _resolve_step_condition(condition, {"github": {"event_name": "issues"}})
    # The runner can now finish it without raising.
    assert runner_module._evaluate_step_if(bound, {"route": {"stage": "triage"}}) is False


def test_literal_binding_escapes_quotes():
    from app.services.workflow_expressions import bind_server_context

    bound = bind_server_context(
        "github.event.issue.title == 'x'",
        {"github": {"event": {"issue": {"title": "it's here"}}}},
    )
    assert bound.startswith("'it\\'s here'")


def test_runner_parser_consumes_both_operands_of_and_or():
    """Short-circuiting in Python also skips consuming the right-hand tokens.

    ``result and self._parse_not()`` never calls the parse when the left side
    is falsy, so the remaining tokens sit unconsumed and the parser reports a
    trailing expression. Only reachable once a falsy operand precedes more
    terms, which binding server context made common.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "runner_for_parser", "src/runners/emulator/runner.py"
    )
    runner_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner_module)

    outputs = {"route": {"stage": "triage"}}
    # The parser takes a context rather than raw step outputs, so that one
    # expression can read steps, inputs, and github.token together.
    context = runner_module._runner_context(None, outputs)
    parse = lambda e: runner_module._StepIfParser(e, context).parse()

    # Falsy left operand, more terms after it.
    assert parse("steps.route.outputs.stage != '' && 'a' == 'b' && ''") is False
    # Truthy left operand of ||, more terms after it.
    assert parse("'a' == 'a' || 'b' == 'c'") is True
    assert parse("'' || steps.route.outputs.stage != ''") is True
