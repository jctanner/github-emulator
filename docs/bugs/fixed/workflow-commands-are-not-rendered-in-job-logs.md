# Bug: workflow commands appear raw in job logs

Status: fixed 2026-11-08.

## Summary

GitHub's runner stores a workflow command in the job log in its rendered
form. `echo "::notice::text"` is stored as `##[notice]text`, and the same
goes for `::warning::`, `::error::`, `::group::` and `::endgroup::`. The
emulator runner uploaded the raw line, so anything that read the log for the
rendered form found nothing.

In Breadboard's behaviour-test T2 tier, three `owners-auth.feature` scenarios
failed this way. Their logs held `::notice::OWNERS file resolved user
'bt-outsider' as approver`, but the suite checks for
`##[notice]OWNERS file resolved user`.

## Fix

The runner's job log sink, `append_log`, renders the commands with
`_render_workflow_commands`. It drops command properties
(`::warning file=a.py,line=3::text` becomes `##[warning]text`). As in the
runner's parser, the properties run to the first `::`, so a property may hold
a colon (`file=C:\a.py`). The message's `%25`, `%0D` and `%0A` escapes are
decoded. Only a command at the start of a line is rendered. `::add-mask::` and `::debug::` are left as
they are.

Tests: `tests/actions/test_workflow_command_log.py`.
