"""Server-side rendering of run:, with: and env: against what GitHub writes.

Compared side by side with github.com:
- a false result is written "false", not "";
- ref_name, ref_type, ref_protected, workflow_sha, repository_visibility,
  repositoryUrl and retention_days have values;
- values only the runner knows (github.workspace, github.job, job.status ...)
  are left for the runner instead of rendering empty.
"""

from app.services.workflow_expressions import evaluate_job_if, ref_facts, render_expressions
from app.services.workflow_service import build_steps_data, static_github_facts

CONTEXT = {
    "github": {
        "event_name": "push",
        "ref": "refs/heads/main",
        "repository": "o/r",
        "event": {"pull_request": {"merged": False}},
        **static_github_facts("refs/heads/main", "abc123", "o/r", False),
    },
    "inputs": {"flag": False, "name": ""},
}


def test_a_false_result_is_written_false():
    assert render_expressions("${{ github.event_name == 'pull_request_target' }}", CONTEXT) == "false"
    assert render_expressions("${{ github.event_name == 'push' }}", CONTEXT) == "true"
    assert render_expressions("${{ github.event.pull_request.merged }}", CONTEXT) == "false"
    assert render_expressions("${{ inputs.flag }}", CONTEXT) == "false"


def test_or_takes_the_first_truthy_alternative_else_the_last():
    assert render_expressions("${{ inputs.name || github.repository }}", CONTEXT) == "o/r"
    assert render_expressions("${{ inputs.name || inputs.flag }}", CONTEXT) == "false"


def test_ref_and_repository_facts_have_values():
    rendered = render_expressions(
        "${{ github.ref_name }} ${{ github.ref_type }} ${{ github.ref_protected }} "
        "${{ github.workflow_sha }} ${{ github.repository_visibility }} ${{ github.retention_days }}",
        CONTEXT,
    )
    assert rendered == "main branch false abc123 public 90"
    assert render_expressions("${{ github.repositoryUrl }}", CONTEXT).endswith("/o/r.git")


def test_ref_facts():
    assert ref_facts("refs/tags/v1.2") == {"ref_name": "v1.2", "ref_type": "tag"}
    assert ref_facts("refs/pull/7/merge") == {"ref_name": "7/merge", "ref_type": "branch"}


def test_values_only_the_runner_knows_are_left_for_it():
    steps = build_steps_data(
        [{
            "run": "cd ${{ github.workspace }} && echo ${{ github.job }} ${{ job.status }} ${{ github.repository }}",
            "env": {"WHERE": "${{ github.event_path }}", "BOTH": "${{ github.action == 'x' && github.ref_name }}"},
            "with": {"path": "${{ github.workspace }}/out"},
        }],
        {}, CONTEXT,
    )
    step = steps[0]
    assert step["run"] == "cd ${{ github.workspace }} && echo ${{ github.job }} ${{ job.status }} o/r"
    assert step["env"]["WHERE"] == "${{ github.event_path }}"
    # The server binds what it knows; the runner finishes the rest.
    assert step["env"]["BOTH"] == "${{ github.action == 'x' && 'main' }}"
    assert step["with"]["path"] == "${{ github.workspace }}/out"


def test_a_server_only_value_still_renders_runner_values_empty():
    # Outside a step (a concurrency group, say) nothing re-evaluates, so the
    # old rendering stands.
    assert render_expressions("${{ github.workspace }}", CONTEXT) == ""


def test_an_operator_over_the_token_is_left_for_the_runner():
    steps = build_steps_data([{"run": "echo ${{ github.token != '' }}"}], {}, CONTEXT)
    assert steps[0]["run"] == "echo ${{ github.token != '' }}"


def test_job_workflow_values_are_the_servers_to_render():
    context = {**CONTEXT, "job": {"workflow_sha": "abc123", "workflow_repository": "o/r"}}
    steps = build_steps_data(
        [{"uses": "actions/checkout@v4",
          "with": {"repository": "${{ job.workflow_repository }}", "ref": "${{ job.workflow_sha }}"}},
         {"run": "echo ${{ job.status }}"}],
        {}, context,
    )
    assert steps[0]["with"] == {"repository": "o/r", "ref": "abc123"}
    assert steps[1]["run"] == "echo ${{ job.status }}"


def test_a_bound_string_quotes_a_quote_the_actions_way():
    context = {**CONTEXT, "inputs": {"title": "Don't"}}
    steps = build_steps_data(
        [{"run": "echo ${{ format('{0}@{1}', inputs.title, github.workspace) }}"}], {}, context,
    )
    assert steps[0]["run"] == "echo ${{ format('{0}@{1}', 'Don''t', github.workspace) }}"


def test_ref_protected_is_a_boolean():
    assert CONTEXT["github"]["ref_protected"] is False
    assert evaluate_job_if("${{ github.ref_protected }}", CONTEXT) is False


def test_working_directory_and_shell_leave_runner_values_for_it():
    steps = build_steps_data(
        [{"run": "make", "working-directory": "${{ github.workspace }}/app"}], {}, CONTEXT,
    )
    assert steps[0]["working-directory"] == "${{ github.workspace }}/app"


def test_an_object_bound_beside_a_runner_value_stays_an_object():
    steps = build_steps_data(
        [{"run": "echo ${{ format('{0} {1}', toJSON(github.event), github.workspace) }}"}], {}, CONTEXT,
    )
    assert "toJSON(fromJSON('{\"pull_request\":{\"merged\":false}}')), github.workspace" in steps[0]["run"]


def _emulator_runner():
    import importlib.util

    spec = importlib.util.spec_from_file_location("runner_for_parity", "src/runners/emulator/runner.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_emulator_runner_finishes_what_the_server_leaves():
    runner = _emulator_runner()
    render = runner._render_local_action
    assert render("${{ format('{0}', 'Don''t') }}", {}, {}) == "Don't"
    assert render("${{ github.workspace == 'nowhere' }}", {}, {}) == "false"
    assert render("echo ${{ job.status }}", {}, {}) == "echo success"
    assert render("echo ${{ job.status }}", {}, {}, job_status="failure") == "echo failure"
