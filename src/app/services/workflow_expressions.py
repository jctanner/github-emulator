"""Safe evaluation and rendering of the supported Actions expression subset."""

import logging
import re

logger = logging.getLogger("github_emulator.workflows.expressions")
_EXPRESSION_RE = re.compile(r"\$\{\{\s*([^}]+?)\s*\}\}")

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


def _expression_truthy(value: object) -> bool:
    return bool(value)


class _IfExpressionParser:
    """Small, safe evaluator for the job-level Actions expression subset."""

    _TOKEN_RE = re.compile(
        r"(?P<space>\s+)|(?P<op>\|\||&&|==|!=|[!(),])|"
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
        result = self._parse_or()
        if self._peek()[0] != "eof":
            raise _ExpressionError("unexpected trailing expression")
        return _expression_truthy(result)

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
            return value

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
            return self._call(token, arguments)

        if token == "true":
            return True
        if token == "false":
            return False
        if token == "null":
            return None
        return _lookup_context_value(self.context, token)

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
        raise _ExpressionError(f"unsupported function {name}")


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


def render_expressions(value: object, context: dict) -> object:
    """Render the small expression subset needed by the M2 runner contract."""
    if isinstance(value, str):
        def replace(match):
            expression = match.group(1).strip()
            # Step outputs only exist after a prior step has run. Preserve the
            # expression for the runner's runtime renderer.
            if expression.startswith("steps.") or expression == "github.token":
                return match.group(0)
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
        return {key: render_expressions(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [render_expressions(item, context) for item in value]
    return value
