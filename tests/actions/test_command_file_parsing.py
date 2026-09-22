"""GITHUB_OUTPUT and GITHUB_ENV accept two forms, not one.

The parser understood only `name=value`. A step that used the heredoc form
succeeded and produced nothing, which is how a routing payload went missing:
the step passed, its output was silently dropped, and the failure appeared much
later as an empty variable inside an agent.
"""

import importlib.util
from pathlib import Path


RUNNER_PATH = (
    Path(__file__).parents[2] / "src" / "runners" / "emulator" / "runner.py"
)
_SPEC = importlib.util.spec_from_file_location("github_emulator_runner", RUNNER_PATH)
assert _SPEC and _SPEC.loader
runner_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runner_module)

read = runner_module.RunnerClient._read_command_file


def _file(tmp_path, text):
    path = tmp_path / "out"
    path.write_text(text)
    return path


def test_the_plain_form_still_works(tmp_path):
    assert read(_file(tmp_path, "stage=triage\ntrigger_source=\n")) == {
        "stage": "triage",
        "trigger_source": "",
    }


def test_a_value_may_contain_equals_signs(tmp_path):
    assert read(_file(tmp_path, "query=a=b=c\n"))["query"] == "a=b=c"


def test_the_heredoc_form_is_read(tmp_path):
    """The shape the dispatch uses for its event payload."""
    payload = '{"issue":{"number":78,"html_url":"https://forge/x/y/issues/78"}}'
    content = read(
        _file(tmp_path, f"event_payload<<PAYLOAD_abc\n{payload}\nPAYLOAD_abc\n")
    )
    assert content["event_payload"] == payload


def test_a_heredoc_value_keeps_its_newlines_and_equals(tmp_path):
    body = "line one\nkey=value\n\nlast line"
    content = read(_file(tmp_path, f"note<<EOF\n{body}\nEOF\n"))
    assert content["note"] == body


def test_both_forms_in_one_file(tmp_path):
    content = read(
        _file(
            tmp_path,
            "stage=triage\n"
            "event_payload<<D\n{\"a\":1}\nD\n"
            "trigger_source=issues\n",
        )
    )
    assert content == {
        "stage": "triage",
        "event_payload": '{"a":1}',
        "trigger_source": "issues",
    }


def test_an_unclosed_delimiter_is_dropped_not_guessed(tmp_path):
    """Inventing a value from a truncated file is worse than having none."""
    content = read(_file(tmp_path, "stage=triage\nbroken<<EOF\nsome text\n"))
    assert content == {"stage": "triage"}
    assert "broken" not in content


def test_a_missing_file_is_empty(tmp_path):
    assert read(tmp_path / "absent") == {}
