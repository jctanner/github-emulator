"""Workflow commands appear in the job log in GitHub's rendered form.

GitHub's runner stores ``echo "::notice::text"`` in the job log as
``##[notice]text``. The emulator runner stored the raw command, so a check
for the rendered line failed. Fullsend's OWNERS behaviour tests look for
``##[notice]OWNERS file resolved user`` in the job log.
"""

import importlib.util
from pathlib import Path


RUNNER_PATH = (
    Path(__file__).parents[2] / "src" / "runners" / "emulator" / "runner.py"
)
_SPEC = importlib.util.spec_from_file_location("github_emulator_runner_commands", RUNNER_PATH)
assert _SPEC and _SPEC.loader
runner_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner_module)
render = runner_module._render_workflow_commands


def test_notice_warning_and_error_are_rendered():
    text = "::notice::resolved\n::warning::careful\n::error::broken\n"
    assert render(text) == "##[notice]resolved\n##[warning]careful\n##[error]broken\n"


def test_command_properties_are_dropped():
    assert render("::warning file=a.py,line=3::semicolon\n") == "##[warning]semicolon\n"


def test_groups_are_rendered():
    assert render("::group::Install\nbody\n::endgroup::\n") == (
        "##[group]Install\nbody\n##[endgroup]\n"
    )


def test_only_a_command_at_the_start_of_a_line_is_rendered():
    text = "say ::notice::not a command\n"
    assert render(text) == text


def test_mask_and_debug_commands_are_left_alone():
    text = "::add-mask::***\n::debug::detail\n"
    assert render(text) == text


def test_a_steps_notice_reaches_the_uploaded_log(monkeypatch, tmp_path):
    monkeypatch.setattr(runner_module, "WORKDIR", str(tmp_path / "work"))
    monkeypatch.setattr(runner_module, "RUNNER_TEMP", str(tmp_path / "temp"))
    client = runner_module.RunnerClient.__new__(runner_module.RunnerClient)
    client._masks = set()
    logs: list[str] = []
    client._upload_logs = lambda repository, job_id, chunk: logs.append(chunk)
    client._report_progress = lambda repository, job_id, steps: None
    client._complete_job = lambda *args, **kwargs: None
    client.execute_job({
        "job_id": 1,
        "repository": "o/r",
        "steps": [{
            "number": 1,
            "name": "auth",
            "run": "echo \"::notice::OWNERS file resolved user 'x' as approver\"",
        }],
    })
    log = "".join(logs)
    assert "##[notice]OWNERS file resolved user 'x' as approver" in log
    assert "::notice::" not in log


def test_a_property_may_hold_a_colon():
    assert render("::warning file=C:\\work\\a.py,line=3::careful\n") == "##[warning]careful\n"


def test_the_message_escapes_are_decoded():
    assert render("::notice::100%25 done%0Anext line\n") == "##[notice]100% done\nnext line\n"
    assert render("::group::a%25b\n") == "##[group]a%b\n"
