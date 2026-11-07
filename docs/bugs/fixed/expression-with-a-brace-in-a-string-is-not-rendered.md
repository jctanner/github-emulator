# Bug: an expression with a `}` in a string literal is not rendered

## Summary

`${{ inputs.artifact-name || format('fullsend-{0}', inputs.agent) }}`, the
artifact name in Fullsend's agent action, reached `actions/upload-artifact`
as its own text, so every Fullsend run uploaded an artifact named
`${{ inputs.artifact-name || format('fullsend-{0}', inputs.agent) }}`.
Anything that finds artifacts by `fullsend-<agent>` found nothing; Fullsend's
behaviour-test CI driver selects them exactly that way.

## Cause

The interpolation pattern, `\$\{\{\s*([^}]+?)\s*\}\}`, refused any `}` in an
expression body, including one inside a quoted string, so the expression was
never matched and so never evaluated. The pattern is defined in the runner
(`src/runners/emulator/runner.py`) and the server
(`src/app/services/workflow_expressions.py`). A third, shadowed copy in
`workflow_service.py` was never used.

## Fix

Both live copies now find expressions with a small scanner instead of a
regular expression; the shadowed copy is removed. Outside a string the old
rule still holds: no `}` before the closing `}}`. A `}` inside a string is
just text. Strings are read as GitHub reads them: a string closes at the
next matching quote, and a backslash is an ordinary character (double quotes
are accepted too, as the emulator's tokenizer accepts them). A string that is never
closed falls back to the old rule, and so does meeting a `${{` anywhere in
the body, in a string or not (GitHub's `${{ '${{' }}` escape still matches,
by the old rule). So every old match whose
strings close still matches, in the same place; a seeded fuzz test checks
this.

The scan is linear also on malformed input. Each position's outcome outside
a string is cached, a quote that runs unclosed to the end of the text marks
every later string it opens as unclosed too, and the fallback's next `}` is
found once per brace. Review rejected three
earlier versions:
- a single pattern backtracked exponentially on a run of quotes (50 took
  over three seconds on the event loop);
- accepting a lone `}` outside strings swallowed a following expression, and
  so did a string holding `}}` or a `${{` (`${{ ' }}' ${{ inputs.a }}`,
  `${{ ' } ${{ inputs.a }}' }}`);
- rescanning after each failed `${{` was quadratic (100 KB took 31 s), and
  so was the fallback's brace search (4 MB took 24 s);
- reading a backslash as an escape carried `${{ 'abc\' }} suffix ' }}` past
  its `}}`.

The emulator's tokenizer accepts backslash escapes and, by backtracking,
reads some strings both ways (`'\''` as one string or as `'\'` and an open
`'`). No single scan agrees with it on every such input, so the scanner
follows GitHub, which has no backslash escapes. Every old match whose
strings close under GitHub's rule still matches.
Regression tests:
`tests/actions/test_expression_braces.py`. Found by the Breadboard
behaviour-test install spike (BT-E2).
