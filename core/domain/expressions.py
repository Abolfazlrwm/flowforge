"""A tiny, safe expression language for conditional branching.

A workflow needs conditions — "run this task only if the amount is over 100" — and
the obvious implementation, ``eval``, is also the wrong one: a definition is data
that comes from an API, so anything that can execute code is a remote code
execution hole with a JSON front-end.

This module is a hand-written tokenizer, parser and evaluator for a language small
enough to read in one screen:

.. code-block:: text

    expr    := or
    or      := and (("||" | "or") and)*
    and     := unary (("&&" | "and") unary)*
    unary   := ("!" | "not") unary | comparison
    comparison := primary (("==" | "!=" | "<" | "<=" | ">" | ">=") primary)?
    primary := NUMBER | STRING | "true" | "false" | "null"
             | PATH | "(" expr ")"
    PATH    := IDENT ("." IDENT)*

That grammar has no calls, no attribute access, no indexing, no assignment and no
statement separator, so the dangerous shapes are not *filtered out* — they cannot be
expressed. ``__import__('os')`` fails at the ``(``; ``x.__class__`` fails because a
path segment must be a plain identifier and ``__class__`` is not special-cased into
anything useful (it is looked up as a key in the caller's own mapping, which never
contains it). The injection tests in ``tests/unit/domain/test_expressions.py`` pin
that down rather than trusting the argument.

Values are JSON-shaped: ``str``, ``int``, ``float``, ``bool``, ``None``, and
mappings for paths. Comparisons are type-checked — ``"a" < 1`` raises instead of
producing Python's arbitrary answer.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final

from core.domain.errors import InvariantViolationError

#: Characters that make up an identifier. A path segment must match this exactly,
#: which is what keeps dunder names out of the language's vocabulary.
_IDENT: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER: Final = re.compile(r"\d+(?:\.\d+)?")
_OPERATORS: Final = ("==", "!=", "<=", ">=", "&&", "||", "<", ">", "!", "(", ")", ".")
_KEYWORDS: Final = frozenset({"true", "false", "null", "and", "or", "not"})

#: Comparison operators, mapped to the function that applies them.
_COMPARISONS: Final = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


class ExpressionError(InvariantViolationError):
    """The expression is malformed, or names something the context does not have."""


@dataclass(frozen=True)
class Token:
    """One lexical unit, with the offset it started at (for error messages)."""

    kind: str  # "number" | "string" | "ident" | "op" | "eof"
    value: Any
    position: int


def _tokenize(source: str) -> list[Token]:
    """Split ``source`` into tokens, or raise :class:`ExpressionError`.

    No escape sequences are supported inside strings: a backslash is an ordinary
    character and a quote ends the literal. That is a deliberate limitation — an
    escape syntax is another parser to get right, and conditions are short.
    """
    tokens: list[Token] = []
    index = 0
    length = len(source)

    while index < length:
        char = source[index]

        if char.isspace():
            index += 1
            continue

        if char in "\"'":
            end = source.find(char, index + 1)
            if end == -1:
                raise ExpressionError(f"unterminated string starting at position {index}")
            tokens.append(Token("string", source[index + 1 : end], index))
            index = end + 1
            continue

        number = _NUMBER.match(source, index)
        if number is not None:
            text = number.group()
            value: float | int = float(text) if "." in text else int(text)
            tokens.append(Token("number", value, index))
            index = number.end()
            continue

        ident = _IDENT.match(source, index)
        if ident is not None:
            tokens.append(Token("ident", ident.group(), index))
            index = ident.end()
            continue

        for operator in _OPERATORS:
            if source.startswith(operator, index):
                tokens.append(Token("op", operator, index))
                index += len(operator)
                break
        else:
            raise ExpressionError(f"unexpected character {char!r} at position {index}")

    tokens.append(Token("eof", None, length))
    return tokens


class _Parser:
    """Recursive-descent parser for the grammar in the module docstring."""

    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._index = 0

    def parse(self) -> Any:
        """Parse a whole expression and require the input to be fully consumed."""
        node = self._or()
        if self._peek().kind != "eof":
            token = self._peek()
            raise ExpressionError(f"unexpected {token.value!r} at position {token.position}")
        return node

    # -- token helpers ----------------------------------------------------

    def _peek(self) -> Token:
        return self._tokens[self._index]

    def _advance(self) -> Token:
        token = self._tokens[self._index]
        self._index += 1
        return token

    def _accept_op(self, *operators: str) -> str | None:
        token = self._peek()
        if token.kind == "op" and token.value in operators:
            self._advance()
            return str(token.value)
        return None

    def _accept_keyword(self, *keywords: str) -> str | None:
        token = self._peek()
        if token.kind == "ident" and token.value in keywords:
            self._advance()
            return str(token.value)
        return None

    # -- grammar ----------------------------------------------------------

    def _or(self) -> Any:
        node = self._and()
        while self._accept_op("||") or self._accept_keyword("or"):
            node = ("binary", "or", node, self._and())
        return node

    def _and(self) -> Any:
        node = self._unary()
        while self._accept_op("&&") or self._accept_keyword("and"):
            node = ("binary", "and", node, self._unary())
        return node

    def _unary(self) -> Any:
        if self._accept_op("!") or self._accept_keyword("not"):
            return ("not", self._unary())
        return self._comparison()

    def _comparison(self) -> Any:
        node = self._primary()
        operator = self._accept_op("==", "!=", "<=", ">=", "<", ">")
        if operator is not None:
            node = ("compare", operator, node, self._primary())
        return node

    def _primary(self) -> Any:  # noqa: PLR0911 - one branch per literal form
        token = self._peek()

        if token.kind == "op" and token.value == "(":
            self._advance()
            node = self._or()
            if self._accept_op(")") is None:
                raise ExpressionError(f"expected ')' at position {self._peek().position}")
            return node

        if token.kind == "number":
            self._advance()
            return ("literal", token.value)

        if token.kind == "string":
            self._advance()
            return ("literal", token.value)

        if token.kind == "ident":
            if token.value == "true":
                self._advance()
                return ("literal", True)
            if token.value == "false":
                self._advance()
                return ("literal", False)
            if token.value == "null":
                self._advance()
                return ("literal", None)
            if token.value in _KEYWORDS:
                raise ExpressionError(
                    f"{token.value!r} cannot start an expression (position {token.position})"
                )
            return self._path()

        raise ExpressionError(f"unexpected {token.value!r} at position {token.position}")

    def _path(self) -> Any:
        segments = [self._advance().value]
        while self._accept_op(".") is not None:
            token = self._peek()
            if token.kind != "ident":
                raise ExpressionError(
                    f"expected a name after '.' at position {token.position}, got {token.value!r}"
                )
            segments.append(self._advance().value)
        return ("path", tuple(segments))


def _resolve(path: tuple[str, ...], context: Mapping[str, Any]) -> Any:
    """Walk ``path`` through ``context``, raising if a segment is absent.

    A missing name is an error rather than ``None``: a typo in a condition would
    otherwise silently make the task never run, which is far harder to notice than
    a failed validation.
    """
    current: Any = context
    for depth, segment in enumerate(path):
        if not isinstance(current, Mapping) or segment not in current:
            traversed = ".".join(path[: depth + 1])
            raise ExpressionError(f"unknown name {traversed!r} in expression context")
        current = current[segment]
    return current


def _truthy(value: Any) -> bool:
    """JSON truthiness: ``false``, ``null``, ``0``, ``""`` and ``{}`` are false."""
    return bool(value)


def _evaluate(node: Any, context: Mapping[str, Any]) -> Any:
    kind = node[0]

    if kind == "literal":
        return node[1]

    if kind == "path":
        return _resolve(node[1], context)

    if kind == "not":
        return not _truthy(_evaluate(node[1], context))

    if kind == "binary":
        operator = node[1]
        if operator == "and":
            left = _evaluate(node[2], context)
            return _truthy(left) and _truthy(_evaluate(node[3], context))
        left = _evaluate(node[2], context)
        return _truthy(left) or _truthy(_evaluate(node[3], context))

    if kind == "compare":
        operator = node[1]
        left = _evaluate(node[2], context)
        right = _evaluate(node[3], context)
        if operator in ("<", "<=", ">", ">="):
            numeric = all(
                isinstance(v, (int, float)) and not isinstance(v, bool) for v in (left, right)
            )
            textual = isinstance(left, str) and isinstance(right, str)
            if not (numeric or textual):
                raise ExpressionError(
                    f"cannot compare {type(left).__name__} and {type(right).__name__} "
                    f"with {operator!r}"
                )
        return bool(_COMPARISONS[operator](left, right))

    raise ExpressionError(f"unrecognised node {kind!r}")  # pragma: no cover - parser invariant


@dataclass(frozen=True)
class Expression:
    """A parsed condition, ready to be evaluated against a context."""

    source: str
    tree: Any

    def evaluate(self, context: Mapping[str, Any]) -> bool:
        """Evaluate against ``context`` and return a bool.

        Raises:
            ExpressionError: if a name is missing or a comparison is ill-typed.
        """
        return _truthy(_evaluate(self.tree, context))


@lru_cache(maxsize=512)
def compile_expression(source: str) -> Expression:
    """Parse ``source`` once and cache it.

    Definitions are validated at publish time, so a bad expression should never
    reach the scheduler — but compiling here means the cost is paid once per
    distinct expression rather than once per task run.

    Raises:
        ExpressionError: if the expression is malformed.
    """
    if not source.strip():
        raise ExpressionError("expression is empty")
    return Expression(source=source, tree=_Parser(_tokenize(source)).parse())


def evaluate_condition(source: str, context: Mapping[str, Any]) -> bool:
    """Convenience wrapper: compile (cached) and evaluate in one call."""
    return compile_expression(source).evaluate(context)
