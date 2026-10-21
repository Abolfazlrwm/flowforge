"""The conditional expression language.

Two things are being protected here, and they are not the same thing:

1. **Correctness** — the operators mean what a workflow author expects, with
   precedence and short-circuiting that match the documented grammar.
2. **Safety** — a definition is data from an API. The tests below feed it the
   shapes that would be remote code execution under ``eval`` and assert that each
   one is a *parse error*, not an execution.
"""

from __future__ import annotations

import pytest
from core.domain.errors import InvariantViolationError
from core.domain.expressions import (
    Expression,
    ExpressionError,
    compile_expression,
    evaluate_condition,
)

CONTEXT = {
    "amount": 250,
    "status": "approved",
    "customer": {"tier": "gold", "active": True},
    "tags": ["a", "b"],
    "empty": "",
    "zero": 0,
    "nothing": None,
}


class TestLiteralsAndComparisons:
    """Comparisons are type-checked, not Python's arbitrary ordering."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("1 == 1", True),
            ("1 == 2", False),
            ("1 != 2", True),
            ("2 > 1", True),
            ("2 >= 2", True),
            ("1 < 2", True),
            ("1 <= 0", False),
            ("'a' == 'a'", True),
            ("'a' < 'b'", True),
            ("true == true", True),
            ("null == null", True),
            ("1.5 > 1", True),
        ],
    )
    def test_comparisons(self, source: str, expected: bool) -> None:
        """Comparisons."""
        assert evaluate_condition(source, {}) is expected

    @pytest.mark.parametrize(
        ("left", "right"),
        [("'a'", "1"), ("true", "1"), ("null", "1"), ("1", "'a'")],
    )
    def test_ordering_across_types_is_refused(self, left: str, right: str) -> None:
        """`"a" < 1` is a mistake in the definition, not a false condition."""
        with pytest.raises(ExpressionError, match="cannot compare"):
            evaluate_condition(f"{left} < {right}", {})

    def test_equality_across_types_is_allowed(self) -> None:
        """`1 == "1"` is legitimately false; it is not an error."""
        assert evaluate_condition("1 == '1'", {}) is False


class TestBooleanLogic:
    """Precedence and short-circuiting match the documented grammar."""

    def test_and_binds_tighter_than_or(self) -> None:
        """And binds tighter than or."""
        # false || (true && true) == true, not (false || true) && true
        assert evaluate_condition("false || true && true", {}) is True
        assert evaluate_condition("false && true || true", {}) is True

    def test_not_binds_tighter_than_and(self) -> None:
        """Not binds tighter than and."""
        assert evaluate_condition("not false && true", {}) is True

    def test_parentheses_override_precedence(self) -> None:
        """Parentheses override precedence."""
        assert evaluate_condition("false && (true || true)", {}) is False

    def test_word_and_symbol_operators_are_equivalent(self) -> None:
        """Word and symbol operators are equivalent."""
        assert evaluate_condition("true and not false", {}) is True
        assert evaluate_condition("true && !false", {}) is True

    def test_or_short_circuits(self) -> None:
        """The right side is never evaluated, so an unknown name cannot fail it."""
        assert evaluate_condition("true || missing.name", {}) is True

    def test_and_short_circuits(self) -> None:
        """And short circuits."""
        assert evaluate_condition("false && missing.name", {}) is False

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("0", False), ("''", False), ("null", False), ("1", True), ("'x'", True)],
    )
    def test_truthiness_of_literals(self, value: str, expected: bool) -> None:
        """Truthiness of literals."""
        assert evaluate_condition(f"!({value})", {}) is (not expected)


class TestPaths:
    """Names resolve through the context mapping, one segment at a time."""

    def test_top_level_name(self) -> None:
        """Top level name."""
        assert evaluate_condition("amount > 100", CONTEXT) is True

    def test_nested_name(self) -> None:
        """Nested name."""
        assert evaluate_condition("customer.tier == 'gold'", CONTEXT) is True

    def test_nested_boolean(self) -> None:
        """Nested boolean."""
        assert evaluate_condition("customer.active", CONTEXT) is True

    def test_unknown_name_is_an_error(self) -> None:
        """A typo must fail loudly, not silently make the task never run."""
        with pytest.raises(ExpressionError, match=r"unknown name 'ammount'"):
            evaluate_condition("ammount > 1", CONTEXT)

    def test_unknown_nested_name_reports_the_path(self) -> None:
        """Unknown nested name reports the path."""
        with pytest.raises(ExpressionError, match=r"customer\.missing"):
            evaluate_condition("customer.missing == 1", CONTEXT)

    def test_descending_into_a_scalar_is_an_error(self) -> None:
        """Descending into a scalar is an error."""
        with pytest.raises(ExpressionError, match=r"amount\.anything"):
            evaluate_condition("amount.anything == 1", CONTEXT)


class TestSyntaxErrors:
    """Malformed input is rejected at parse time with a position."""

    @pytest.mark.parametrize(
        "source",
        [
            "",
            "   ",
            "1 ==",
            "== 1",
            "(1 == 1",
            "1 == 1)",
            "'unterminated",
            "1 & 1",
            "a b",
            ".",
            "1..2",
            "and",
            "not",
        ],
    )
    def test_malformed_expressions_raise(self, source: str) -> None:
        """Malformed expressions raise."""
        with pytest.raises(ExpressionError):
            compile_expression(source)

    def test_a_trailing_token_is_rejected(self) -> None:
        """A trailing token is rejected."""
        with pytest.raises(ExpressionError, match="unexpected"):
            compile_expression("1 == 1 2")

    def test_the_error_names_the_position(self) -> None:
        """The error names the position."""
        with pytest.raises(ExpressionError, match="position"):
            compile_expression("1 == #")


class TestInjection:
    """Shapes that would be code execution under ``eval`` must be parse errors.

    Each string below is a working ``eval`` payload. If any of them returns a
    value instead of raising, the language has grown a hole.
    """

    @pytest.mark.parametrize(
        "payload",
        [
            "__import__('os').system('touch /tmp/pwned')",
            "__import__('os')",
            "open('/etc/passwd').read()",
            "().__class__.__bases__[0].__subclasses__()",
            "''.join(['a'])",
            "eval('1')",
            "exec('1')",
            "globals()",
            "locals()",
            "getattr(object, '__class__')",
            "amount.__class__",
            "amount.__class__.__mro__",
            "[x for x in range(3)]",
            "lambda: 1",
            "1; import os",
            "1 or __import__('os')",
            "true and open('/etc/passwd')",
            "`id`",
            "1 # comment",
            "${7*7}",
            "{{7*7}}",
        ],
    )
    def test_payloads_never_produce_a_value(self, payload: str) -> None:
        """Each payload must fail — at parse time or at evaluation, never silently.

        `amount.__class__` parses as a legal path and then fails to resolve, which is
        the interesting case: the language has no attribute access at all, so a
        dunder segment is just a missing key.
        """
        with pytest.raises(ExpressionError):
            evaluate_condition(payload, CONTEXT)

    def test_a_dunder_name_is_only_a_context_key(self) -> None:
        """`__class__` is not special — it resolves (and fails) as a plain key."""
        with pytest.raises(ExpressionError, match="unknown name '__class__'"):
            evaluate_condition("__class__ == 1", CONTEXT)

    def test_the_payload_cannot_reach_a_real_object(self, tmp_path: object) -> None:
        """A payload naming a real file must still not touch it."""
        with pytest.raises(ExpressionError):
            evaluate_condition("open('/etc/passwd')", CONTEXT)


class TestCaching:
    """Compilation is cached, so the scheduler pays it once per distinct source."""

    def test_the_same_source_compiles_to_the_same_object(self) -> None:
        """The same source compiles to the same object."""
        first = compile_expression("amount > 100")
        second = compile_expression("amount > 100")

        assert first is second
        assert isinstance(first, Expression)

    def test_expression_is_immutable(self) -> None:
        """Expression is immutable."""
        expression = compile_expression("amount > 100")

        # A frozen dataclass raises FrozenInstanceError, which is a TypeError.
        with pytest.raises(AttributeError):
            expression.source = "changed"  # type: ignore[misc]

    def test_expression_error_is_an_invariant_violation(self) -> None:
        """It is a definition problem, so it maps to the API's validation error."""
        assert issubclass(ExpressionError, InvariantViolationError)
