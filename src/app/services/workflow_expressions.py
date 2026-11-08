"""Safe evaluation and rendering of the supported Actions expression subset."""

import json
import logging
import re

logger = logging.getLogger("github_emulator.workflows.expressions")
# One ``${{ ... }}``, found by a scanner rather than a regular expression.
#
# The body may hold a ``}`` inside a string literal, as in
# ``format('fullsend-{0}', inputs.agent)``. The old pattern,
# ``\$\{\{\s*([^}]+?)\s*\}\}``, refused any ``}``, so such an expression was
# never matched and reached the step as its own text.
#
# The scanner keeps the old rule everywhere outside a string: no ``}`` until
# the closing ``}}``. Strings are read the way the expression tokenizer reads
# them as GitHub does: a string closes at the next matching quote, and a
# backslash is an ordinary character. A ``}`` inside one is just text.
# (The tokenizer also accepts backslash escapes, but backtracks to this
# reading when an escape cannot close the string, so treating a backslash
# as an escape here would carry an expression past the ``}}`` that ends it.) A string that is never closed makes the
# expression fall back to the old rule, so anything the old pattern matched
# still matches.
#
# The work is linear in the text, also on malformed input, which a single
# regular expression could not manage (a run of quotes or spaces in an
# unclosed expression backtracked; one failed ``${{`` after another rescanned
# the rest of the text):
# - outside a string, the outcome from a position does not depend on which
#   ``${{`` the scan began at, so each position's outcome is worked out once;
# - once a string opened by a quote runs unclosed to the end of the text, so
#   does every later string opened by the same quote, because the scan from
#   there lines up with the first one;
# - the fallback's next ``}`` is found once per brace, as body starts only
#   move forward.
#
# A ``${{`` ends the string-aware reading, outside a string or in one, and
# the old rule decides instead. So a string holding ``}}`` cannot carry one
# expression over the next, and GitHub's ``${{ '${{' }}`` escape matches as
# it always did. The unclosed-quote shortcut is recorded only for a string
# that really runs to the end of the text.
#
# ``group(1)`` is the stripped body and ``group(0)`` the whole text, as with
# the regular expression this replaces.


class _ExpressionMatch:
    __slots__ = ("_whole", "_body")

    def __init__(self, whole: str, body: str):
        self._whole = whole
        self._body = body

    def group(self, index: int = 0) -> str:
        if index == 0:
            return self._whole
        if index == 1:
            return self._body
        raise IndexError(index)


def _string_aware_close(text: str, position: int, outcomes: dict, unclosed: dict):
    """Where the ``}}`` closing a body from ``position`` starts, or None."""
    length = len(text)
    visited = []
    index = position
    while True:
        if index in outcomes:
            result = outcomes[index]
            break
        visited.append(index)
        if index >= length:
            result = None
            break
        char = text[index]
        if char == "}":
            result = index if text.startswith("}}", index) else None
            break
        if char == "$" and text.startswith("${{", index):
            # Never part of an expression outside a string. Running past it
            # would swallow the next expression into this one.
            result = None
            break
        if char in "'\"":
            if unclosed.get(char, length) < index:
                result = None
                break
            closing = text.find(char, index + 1)
            if closing < 0:
                closing = length
            nested = text.find("${{", index + 1, closing) >= 0
            cursor = closing
            if nested:
                # A ``${{`` inside a string: leave it to the old rule, which
                # matches GitHub's ``${{ '${{' }}`` escape and never lets a
                # string carry this expression over the next one.
                result = None
                break
            if cursor >= length:
                unclosed[char] = min(unclosed.get(char, length), index)
                result = None
                break
            index = cursor + 1
            continue
        index += 1
    for seen in visited:
        outcomes[seen] = result
    return result


class _ExpressionScanner:
    def _matches(self, text: str):
        outcomes: dict = {}
        unclosed: dict = {}
        brace = -1
        position = 0
        while True:
            start = text.find("${{", position)
            if start < 0:
                return
            body_start = start + 3
            end = _string_aware_close(text, body_start, outcomes, unclosed)
            if end is None:
                # Body starts only move forward, so the next brace is found
                # once per brace, not once per failed ``${{``.
                if brace < body_start and brace != len(text):
                    brace = text.find("}", body_start)
                    if brace < 0:
                        brace = len(text)
                if brace < len(text) and text.startswith("}}", brace):
                    end = brace
            if end is None or end == body_start:
                position = start + 1
                continue
            yield start, end + 2, _ExpressionMatch(text[start:end + 2], text[body_start:end].strip())
            position = end + 2

    def finditer(self, text: str):
        for _start, _end, match in self._matches(text):
            yield match

    def sub(self, replace, text: str) -> str:
        parts = []
        position = 0
        for start, end, match in self._matches(text):
            parts.append(text[position:start])
            parts.append(replace(match))
            position = end
        parts.append(text[position:])
        return "".join(parts)


_EXPRESSION_RE = _ExpressionScanner()

# An interpolated expression needs the full parser once it contains an
# operator, a string literal, or a call. Bare context paths, including the
# ``a || b`` fallback chain, stay on the cheaper lookup path.
_NEEDS_PARSER_RE = re.compile(r"\(|&&|==|!=|'|\"")
# A reference to the runner context root, as a whole path segment.
_RUNNER_CONTEXT_RE = re.compile(r"(?<![\w.])runner\.")

def _lookup_context(context: dict, expression: str) -> str:
    value = _lookup_context_value(context, expression)
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        import json
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _lookup_context_value(context: dict, expression: str) -> object:
    value: object = context
    for part in expression.strip().split("."):
        if isinstance(value, dict):
            value = value.get(part, "")
        else:
            return ""
    return value

class _ExpressionError(ValueError):
    pass


def _index_value(value: object, key: object) -> object:
    """Index into a mapping or sequence, returning None when absent.

    Mirrors Actions, where reading a missing property yields null rather than
    raising, so a condition such as ``fromJSON(x).include[0] != null`` can be
    written against data that may not be there.
    """
    if isinstance(value, dict):
        return value.get(str(key))
    if isinstance(value, (list, tuple)):
        try:
            return value[int(key)]
        except (ValueError, TypeError, IndexError):
            return None
    return None


class _Filtered(list):
    """The result of an object filter, to which later accessors apply
    element-wise."""


_MISSING = object()


def _index_present(value: object, key: object) -> object:
    """Like ``_index_value``, but tells a missing property (``_MISSING``)
    from one that is present and null."""
    if isinstance(value, dict):
        return value.get(str(key), _MISSING)
    if isinstance(value, (list, tuple)):
        try:
            return value[int(key)]
        except (ValueError, TypeError, IndexError):
            return _MISSING
    return _MISSING


def _index_any(value: object, key: object) -> object:
    if isinstance(value, _Filtered):
        return _Filtered(
            item for item in (_index_present(e, key) for e in value)
            if item is not _MISSING
        )
    return _index_value(value, key)


def _object_filter(value: object) -> "_Filtered":
    """The elements an object filter selects: an array's items or an
    object's values, taken from each element once already filtered."""
    out = _Filtered()
    for element in (value if isinstance(value, _Filtered) else [value]):
        if isinstance(element, list):
            out.extend(element)
        elif isinstance(element, dict):
            out.extend(element.values())
    return out


# The job status functions. A job-level condition without any of them is
# evaluated as ``success() && (condition)`` on GitHub, and its job is skipped
# when a job it needs did not succeed; one that names a status function is
# evaluated even then (``always()``, ``!cancelled()``, ``failure()``).
_STATUS_FUNCTIONS = ("success", "failure", "cancelled", "always")
_STATUS_CALL_RE = re.compile(r"\b(?:success|failure|cancelled|always)\s*\(\s*\)")

# Context keys the scheduler supplies for the status functions: the job's own
# ``needs:`` keys and whether the run was cancelled.
JOB_NEEDS_KEY = "__job_needs__"
RUN_CANCELLED_KEY = "__run_cancelled__"


def uses_status_function(condition: object) -> bool:
    """Whether a job-level ``if`` names a status function."""
    return isinstance(condition, str) and bool(_STATUS_CALL_RE.search(condition))


def job_status(name: str, context: dict) -> bool:
    """``success()``, ``failure()``, ``cancelled()`` or ``always()`` for a
    job, from the results of the jobs it needs."""
    if name == "always":
        return True
    cancelled = bool(context.get(RUN_CANCELLED_KEY))
    if name == "cancelled":
        return cancelled
    needs = context.get("needs") or {}
    keys = context.get(JOB_NEEDS_KEY)
    if keys is None:
        keys = list(needs)
    results = [str((needs.get(key) or {}).get("result") or "") for key in keys]
    if name == "failure":
        return any(result == "failure" for result in results)
    return not cancelled and all(result == "success" for result in results)


def _string(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _number(value: object) -> float:
    """Actions' coercion of a value to a number for a mixed-type comparison."""
    if value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        try:
            if text.lower().startswith("0x"):
                return float(int(text, 16))
            return float(text)
        except ValueError:
            return float("nan")
    return float("nan")  # arrays and objects


def _compare(operator: str, left: object, right: object) -> bool:
    """Actions' comparison: strings case-insensitively; values of different
    types (null, booleans, numbers, strings) as numbers, so ``null == ''``;
    arrays and objects equal only to themselves."""
    if isinstance(left, (list, dict)) or isinstance(right, (list, dict)):
        if operator == "==":
            return left is right
        if operator == "!=":
            return left is not right
        return False
    if isinstance(left, str) and isinstance(right, str):
        a, b = left.lower(), right.lower()
    elif type(left) is type(right) and left is not None and not isinstance(left, (int, float)):
        a, b = left, right
    else:
        a, b = _number(left), _number(right)
        if a != a or b != b:  # NaN: every comparison but != is false
            return operator == "!="
    if operator == "==":
        return a == b
    if operator == "!=":
        return a != b
    if operator == "<":
        return a < b
    if operator == ">":
        return a > b
    if operator == "<=":
        return a <= b
    return a >= b


def _parse_json(value: object) -> object:
    """Implement ``fromJSON``: parse a JSON string, passing through non-strings."""
    if isinstance(value, (dict, list)):
        return value
    text = "" if value is None else str(value)
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _expression_truthy(value: object) -> bool:
    return bool(value)


def _format_expression(template: object, values: list) -> str:
    """Implement Actions' ``format``: ``{0}`` placeholders, ``{{`` escapes.

    Not ``str.format``, which would honour attribute and index access inside a
    placeholder. Actions does not, and reaching those from workflow text is not
    something to offer by accident.
    """
    text = "" if template is None else str(template)
    out = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in "{}" and text[index:index + 2] == char * 2:
            out.append(char)
            index += 2
            continue
        if char != "{":
            out.append(char)
            index += 1
            continue
        end = text.find("}", index)
        if end == -1:
            raise _ExpressionError("unterminated placeholder in format()")
        digits = text[index + 1:end]
        if not digits.isdigit():
            raise _ExpressionError(f"format() placeholder {digits!r} is not a number")
        position = int(digits)
        if position >= len(values):
            raise _ExpressionError(f"format() has no argument {position}")
        value = values[position]
        out.append("" if value is None else str(value))
        index = end + 1
    return "".join(out)


class _IfExpressionParser:
    """Small, safe evaluator for the job-level Actions expression subset."""

    # ``.`` and ``[``/``]`` are tokens so that property and index access can
    # follow a function call, as in ``fromJSON(x).issue.number`` and
    # ``fromJSON(x).include[0]``. A dotted context path such as
    # ``github.event.action`` still tokenizes as a single name, because the
    # name alternative requires a leading letter and consumes its own dots.
    _TOKEN_RE = re.compile(
        r"(?P<space>\s+)|(?P<op>\|\||&&|==|!=|<=|>=|[<>!(),\[\].*])|"
        r"(?P<string>'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\")|"
        r"(?P<number>\d+(?:\.\d+)?)|"
        # A dotted name never ends in a dot, so ``include.*.agent`` leaves
        # the ``.`` and ``*`` of its object filter as tokens of their own.
        r"(?P<name>[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*)"
    )

    def __init__(self, expression: str, context: dict):
        self.context = context
        self.tokens = self._tokenize(expression)
        self.position = 0

    @classmethod
    def _tokenize(cls, expression: str) -> list[tuple[str, str]]:
        tokens = []
        position = 0
        while position < len(expression):
            match = cls._TOKEN_RE.match(expression, position)
            if not match:
                raise _ExpressionError(f"unsupported character at {position}")
            position = match.end()
            kind = match.lastgroup
            if kind != "space":
                tokens.append((kind, match.group(0)))
        tokens.append(("eof", ""))
        return tokens

    def _peek(self, value: str | None = None) -> tuple[str, str] | bool:
        token = self.tokens[self.position]
        return token[1] == value if value is not None else token

    def _take(self, value: str | None = None) -> tuple[str, str]:
        token = self.tokens[self.position]
        if value is not None and token[1] != value:
            raise _ExpressionError(f"expected {value!r}")
        self.position += 1
        return token

    def parse(self) -> bool:
        return _expression_truthy(self.evaluate())

    def evaluate(self) -> object:
        """Evaluate to the underlying value rather than a boolean."""
        result = self._parse_or()
        if self._peek()[0] != "eof":
            raise _ExpressionError("unexpected trailing expression")
        return result

    def _parse_or(self) -> object:
        result = self._parse_and()
        while self._peek("||"):
            self._take("||")
            right = self._parse_and()
            result = result or right
        return result

    def _parse_and(self) -> object:
        result = self._parse_not()
        while self._peek("&&"):
            self._take("&&")
            right = self._parse_not()
            result = result and right
        return result

    def _parse_not(self) -> object:
        if self._peek("!"):
            self._take("!")
            return not _expression_truthy(self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self) -> object:
        left = self._parse_primary()
        for operator in ("==", "!=", "<=", ">=", "<", ">"):
            if self._peek(operator):
                self._take(operator)
                right = self._parse_primary()
                return _compare(operator, left, right)
        return left

    def _parse_primary(self) -> object:
        if self._peek("("):
            self._take("(")
            value = self._parse_or()
            self._take(")")
            return self._parse_accessors(value)

        kind, token = self._take()
        if kind == "string":
            return token[1:-1].replace("\\'", "'").replace('\\"', '"')
        if kind == "number":
            return float(token) if "." in token else int(token)
        if kind != "name":
            raise _ExpressionError("expected value")

        if self._peek("("):
            self._take("(")
            arguments = []
            if not self._peek(")"):
                arguments.append(self._parse_or())
                while self._peek(","):
                    self._take(",")
                    arguments.append(self._parse_or())
            self._take(")")
            return self._parse_accessors(self._call(token, arguments))

        if token == "true":
            return True
        if token == "false":
            return False
        if token == "null":
            return None
        return self._parse_accessors(_lookup_context_value(self.context, token))

    def _parse_accessors(self, value: object) -> object:
        """Apply any trailing ``.name``, ``[index]`` and object-filter
        (``.*``, ``[*]``) accessors to a value.

        A filter yields a ``_Filtered`` list, and an accessor applied to one
        applies to each element, keeping those that have the property (even
        when it is null): on GitHub ``fromJSON(m).include.*.agent`` is the
        list of every entry's agent. Being a type rather than parser state,
        that survives parentheses, as in ``(x.*).name``.
        """
        while True:
            if self._peek("."):
                self._take(".")
                if self._peek("*"):
                    self._take("*")
                    value = _object_filter(value)
                    continue
                kind, token = self._take()
                if kind != "name":
                    raise _ExpressionError("expected property name")
                # A dotted name token such as ``issue.html_url`` arrives whole.
                for part in token.split("."):
                    value = _index_any(value, part)
            elif self._peek("["):
                self._take("[")
                if self._peek("*"):
                    self._take("*")
                    self._take("]")
                    value = _object_filter(value)
                    continue
                key = self._parse_or()
                self._take("]")
                value = _index_any(value, key)
            else:
                return value

    def _call(self, name: str, arguments: list[object]) -> object:
        if name in _STATUS_FUNCTIONS and not arguments:
            return job_status(name, self.context)
        if name == "startsWith" and len(arguments) == 2:
            return _string(arguments[0]).lower().startswith(_string(arguments[1]).lower())
        if name == "endsWith" and len(arguments) == 2:
            return _string(arguments[0]).lower().endswith(_string(arguments[1]).lower())
        if name == "contains" and len(arguments) == 2:
            haystack, needle = arguments
            # Case-insensitive, as on GitHub: a string contains a substring;
            # an array contains an item equal to the needle.
            if isinstance(haystack, list):
                return any(_compare("==", item, needle) for item in haystack)
            if isinstance(haystack, dict):
                return False
            return _string(needle).lower() in _string(haystack).lower()
        if name == "format" and arguments:
            return _format_expression(arguments[0], arguments[1:])
        if name == "fromJSON" and len(arguments) == 1:
            return _parse_json(arguments[0])
        if name == "toJSON" and len(arguments) == 1:
            return json.dumps(arguments[0], separators=(",", ":"), sort_keys=True)
        raise _ExpressionError(f"unsupported function {name}")


# Context roots the server can resolve. ``steps`` is deliberately absent: only
# the runner knows step outputs.
_SERVER_CONTEXT_ROOTS = ("github", "inputs", "needs", "vars", "secrets", "matrix", "strategy", "job")


def _as_literal(value: object) -> str:
    """Render a resolved value as an expression literal."""
    if value is None:
        return "''"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list)):
        # These appear as truthiness tests, such as
        # ``github.event.issue.pull_request``, so collapse to a boolean rather
        # than inventing a comparable string.
        return "true" if value else "false"
    text = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{text}'"


def _json_literal(value: object) -> str:
    """An array or object as an expression the runner can evaluate. Actions
    has no array literal, so it is ``fromJSON('...')``, quoted the Actions
    way (``''`` for a quote)."""
    text = json.dumps(value, separators=(",", ":")).replace("'", "''")
    return f"fromJSON('{text}')"


def _accessor_chain_end(tokens: list, index: int) -> int:
    """The index of the last token of the accessors directly after the name
    at ``index`` (``.name``, ``.*``, ``[...]``), or ``index`` when there are
    none. A bracket whose contents read anything but literals and
    server-side contexts ends the chain before it."""
    last = index
    j = index + 1
    while j < len(tokens):
        text = tokens[j][1]
        if text == "." and j + 1 < len(tokens) and (
            tokens[j + 1][0] == "name" or tokens[j + 1][1] == "*"
        ):
            last = j = j + 1
            j += 1
            continue
        if text == "[":
            depth, k = 0, j
            while k < len(tokens):
                if tokens[k][1] == "[":
                    depth += 1
                elif tokens[k][1] == "]":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            if k >= len(tokens):
                break
            inner = tokens[j + 1:k]
            if any(
                t[0] == "name" and t[1] not in ("true", "false", "null")
                and t[1].split(".", 1)[0] not in _SERVER_CONTEXT_ROOTS
                for t in inner
            ):
                break
            last = j = k
            j += 1
            continue
        break
    return last


def bind_server_context(expression: str, context: dict) -> str:
    """Substitute server-resolvable context paths, leaving ``steps.*`` intact.

    A step condition may mix both, as in
    ``steps.route.outputs.stage != '' && github.event_name == 'issue_comment'``.
    Handing the whole thing to the runner leaves it unable to resolve the
    ``github.*`` half; deciding it on the server is impossible because of the
    ``steps.*`` half. Binding what is known here produces an expression the
    runner can finish on its own.
    """
    out = []
    position = 0
    tokens = []
    while position < len(expression):
        match = _IfExpressionParser._TOKEN_RE.match(expression, position)
        if not match:
            return expression
        tokens.append((match.lastgroup, match.group(0), match.start(), match.end()))
        position = match.end()

    skip_until = -1
    for index, (kind, text, start, end) in enumerate(tokens):
        if index <= skip_until:
            continue
        if kind != "name" or text in ("true", "false", "null"):
            continue
        # A name followed by "(" is a function call, not a context path.
        following = next(
            (t for t in tokens[index + 1:] if t[0] != "space"), None
        )
        if following and following[1] == "(":
            continue
        if text.split(".", 1)[0] not in _SERVER_CONTEXT_ROOTS:
            continue
        # A path with trailing accessors (``labels.*.name``, ``labels[0]``)
        # is bound whole: binding the path alone turned the array into
        # ``true`` and left ``true.*.name`` for the runner.
        last = _accessor_chain_end(tokens, index)
        if last > index:
            span = expression[start:tokens[last][3]]
            try:
                value = _IfExpressionParser(span, context).evaluate()
            except _ExpressionError:
                value = _MISSING
            if value is not _MISSING:
                literal = (
                    _json_literal(value) if isinstance(value, (dict, list))
                    else _as_literal(value)
                )
                out.append((start, tokens[last][3], literal))
                skip_until = last
                continue
        out.append((start, end, _as_literal(_lookup_context_value(context, text))))

    if not out:
        return expression
    rendered = []
    cursor = 0
    for start, end, literal in out:
        rendered.append(expression[cursor:start])
        rendered.append(literal)
        cursor = end
    rendered.append(expression[cursor:])
    return "".join(rendered)


def evaluate_job_if(condition: object, context: dict) -> bool:
    """Evaluate a job-level ``if`` condition using Actions-like semantics."""
    if condition is None:
        return True
    if isinstance(condition, bool):
        return condition
    if not isinstance(condition, str):
        return _expression_truthy(condition)

    expression = condition.strip()
    if expression.startswith("${{") and expression.endswith("}}"):
        expression = expression[3:-2].strip()
    try:
        return _IfExpressionParser(expression, context).parse()
    except _ExpressionError as exc:
        logger.warning("Unable to evaluate job condition %r: %s", condition, exc)
        return False


def evaluate_expression_value(value: object, context: dict) -> object:
    """A value that is one whole expression, evaluated to what it yields
    (an object for ``${{ fromJSON(...) }}``) rather than rendered to text.
    Anything else is rendered as usual."""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("${{") and text.endswith("}}") and text.count("${{") == 1:
            try:
                return _IfExpressionParser(text[3:-2].strip(), context).evaluate()
            except _ExpressionError as exc:
                logger.warning("Unable to evaluate expression %r: %s", value, exc)
                return None
    return render_expressions(value, context)


def render_expressions(value: object, context: dict, resolve_steps: bool = False) -> object:
    """Render the small expression subset needed by the M2 runner contract.

    ``resolve_steps`` renders ``steps.*`` references instead of preserving
    them. Job ``outputs:`` are resolved server-side after the job finishes, at
    which point its step outputs are known, so that caller opts in.
    """
    if isinstance(value, str):
        def replace(match):
            expression = match.group(1).strip()
            # Step outputs only exist after a prior step has run. Preserve the
            # expression for the runner's runtime renderer.
            if expression == "github.token":
                return match.group(0)
            if expression.startswith("steps.") and not resolve_steps:
                return match.group(0)
            # The runner context describes the machine a job lands on, so
            # only the runner can resolve it. Rendering it here produced an
            # empty string: ``${{ runner.temp }}/fullsend-cache`` reached the
            # runner as ``/fullsend-cache`` and actions/cache saved nothing.
            if _RUNNER_CONTEXT_RE.search(expression):
                return match.group(0)
            # Anything with an operator, a literal, or a call needs the real
            # parser. A job's ``outputs:`` routinely combines several step
            # outputs, as in
            # ``a != 'true' && b != 'true' && steps.x.outputs.stage || ''``;
            # the path-lookup fallback below cannot evaluate that and returned
            # empty, which silently erased the routing decision. Plain context
            # paths keep the cheaper lookup so their behaviour is unchanged.
            if _NEEDS_PARSER_RE.search(expression):
                try:
                    value = _IfExpressionParser(expression, context).evaluate()
                except _ExpressionError as exc:
                    logger.warning(
                        "Unable to render expression %r: %s", expression, exc
                    )
                    return ""
                if value is None or value is False:
                    return ""
                if value is True:
                    return "true"
                if isinstance(value, (dict, list)):
                    return json.dumps(value, separators=(",", ":"))
                return str(value)
            # Keep one workflow usable for both an automatic event and an
            # explicit workflow_dispatch. This is the common GitHub Actions
            # fallback form used by the Fullsend fixtures.
            for alternative in expression.split("||"):
                resolved = _lookup_context(context, alternative)
                if resolved:
                    return resolved
            return ""
        return _EXPRESSION_RE.sub(replace, value)
    if isinstance(value, dict):
        return {
            key: render_expressions(item, context, resolve_steps)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [render_expressions(item, context, resolve_steps) for item in value]
    return value
