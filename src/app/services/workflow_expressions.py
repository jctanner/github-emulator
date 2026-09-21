"""Safe evaluation and rendering of the supported Actions expression subset."""

import json
import logging
import re

logger = logging.getLogger("github_emulator.workflows.expressions")
_EXPRESSION_RE = re.compile(r"\$\{\{\s*([^}]+?)\s*\}\}")

# An interpolated expression needs the full parser once it contains an
# operator, a string literal, or a call. Bare context paths, including the
# ``a || b`` fallback chain, stay on the cheaper lookup path.
_NEEDS_PARSER_RE = re.compile(r"\(|&&|==|!=|'|\"")

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
        r"(?P<space>\s+)|(?P<op>\|\||&&|==|!=|[!(),\[\].])|"
        r"(?P<string>'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\")|"
        r"(?P<number>\d+(?:\.\d+)?)|(?P<name>[A-Za-z_][A-Za-z0-9_.-]*)"
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
        if self._peek("==") or self._peek("!="):
            operator = self._take()[1]
            right = self._parse_primary()
            equal = left == right
            return equal if operator == "==" else not equal
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
        """Apply any trailing ``.name`` and ``[index]`` accessors to a value."""
        while True:
            if self._peek("."):
                self._take(".")
                kind, token = self._take()
                if kind != "name":
                    raise _ExpressionError("expected property name")
                # A dotted name token such as ``issue.html_url`` arrives whole.
                for part in token.split("."):
                    value = _index_value(value, part)
            elif self._peek("["):
                self._take("[")
                index = self._parse_or()
                self._take("]")
                value = _index_value(value, index)
            else:
                return value

    @staticmethod
    def _call(name: str, arguments: list[object]) -> object:
        if name == "startsWith" and len(arguments) == 2:
            return str(arguments[0] or "").startswith(str(arguments[1] or ""))
        if name == "endsWith" and len(arguments) == 2:
            return str(arguments[0] or "").endswith(str(arguments[1] or ""))
        if name == "contains" and len(arguments) == 2:
            haystack, needle = arguments
            return needle in haystack if isinstance(haystack, (list, dict, str)) else False
        if name == "always" and not arguments:
            return True
        if name == "format" and arguments:
            return _format_expression(arguments[0], arguments[1:])
        if name == "fromJSON" and len(arguments) == 1:
            return _parse_json(arguments[0])
        if name == "toJSON" and len(arguments) == 1:
            return json.dumps(arguments[0], separators=(",", ":"), sort_keys=True)
        raise _ExpressionError(f"unsupported function {name}")


# Context roots the server can resolve. ``steps`` is deliberately absent: only
# the runner knows step outputs.
_SERVER_CONTEXT_ROOTS = ("github", "inputs", "needs", "vars", "secrets", "matrix", "job")


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

    for index, (kind, text, start, end) in enumerate(tokens):
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
