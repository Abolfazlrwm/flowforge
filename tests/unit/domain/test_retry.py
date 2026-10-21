# A seeded, non-cryptographic PRNG is exactly what these tests need: the backoff
# jitter is not a security control, and determinism is what makes it assertable.
# ruff: noqa: S311

"""Retry policy and backoff arithmetic.

These are pure-domain tests: no database, no clock. The interesting behaviour is
the cap, the growth factor, and the fact that full jitter stays inside the
interval it promises — an out-of-range delay would either retry too eagerly or
silently exceed the operator's ceiling.
"""

from __future__ import annotations

import random

import pytest
from core.domain.errors import InvariantViolationError
from core.domain.retry import (
    DEFAULT_RETRY_POLICY,
    RetryPolicy,
    calculate_backoff,
    get_retry_policy,
)

#: Jitter off, so the arithmetic is exact rather than a range.
DETERMINISTIC = RetryPolicy(max_attempts=3, base_seconds=1.0, max_seconds=60.0, jitter=False)


class TestRetryPolicyValidation:
    """A policy that cannot be enforced is rejected at construction."""

    def test_defaults_are_usable(self) -> None:
        """The defaults must be a valid policy, or every task without one breaks."""
        assert DEFAULT_RETRY_POLICY.max_attempts == 3
        assert DEFAULT_RETRY_POLICY.base_seconds == 1.0
        assert DEFAULT_RETRY_POLICY.max_seconds == 60.0
        assert DEFAULT_RETRY_POLICY.jitter is True

    @pytest.mark.parametrize("max_attempts", [0, -1])
    def test_max_attempts_below_one_is_refused(self, max_attempts: int) -> None:
        """Zero attempts would mean a task can never run at all."""
        with pytest.raises(InvariantViolationError, match="max_attempts"):
            RetryPolicy(max_attempts=max_attempts)

    def test_negative_base_seconds_is_refused(self) -> None:
        """A negative delay would schedule the retry in the past."""
        with pytest.raises(InvariantViolationError, match="base_seconds"):
            RetryPolicy(base_seconds=-1.0)

    def test_max_seconds_below_base_seconds_is_refused(self) -> None:
        """A cap below the first delay would make the cap meaningless."""
        with pytest.raises(InvariantViolationError, match="max_seconds"):
            RetryPolicy(base_seconds=10.0, max_seconds=5.0)

    def test_zero_base_seconds_is_allowed(self) -> None:
        """Zero means "retry immediately", which is a legitimate policy."""
        policy = RetryPolicy(base_seconds=0.0, max_seconds=0.0)
        assert policy.base_seconds == 0.0


class TestGetRetryPolicy:
    """The policy is read from ``config["retry"]``, per task."""

    @pytest.mark.parametrize("config", [None, {}, {"retry": None}, {"retry": "fast"}])
    def test_absent_or_malformed_block_yields_the_default(self, config: dict | None) -> None:
        """A task that says nothing about retries gets the documented default."""
        assert get_retry_policy(config) is DEFAULT_RETRY_POLICY

    def test_an_explicit_block_is_read(self) -> None:
        """Every documented key is honoured."""
        policy = get_retry_policy(
            {"retry": {"max_attempts": 5, "base_seconds": 2.5, "max_seconds": 30, "jitter": False}}
        )

        assert policy == RetryPolicy(
            max_attempts=5, base_seconds=2.5, max_seconds=30.0, jitter=False
        )

    def test_a_partial_block_falls_back_per_key(self) -> None:
        """Only the keys given are overridden; the rest keep the defaults."""
        policy = get_retry_policy({"retry": {"max_attempts": 7}})

        assert policy.max_attempts == 7
        assert policy.base_seconds == DEFAULT_RETRY_POLICY.base_seconds
        assert policy.max_seconds == DEFAULT_RETRY_POLICY.max_seconds

    def test_unknown_keys_are_ignored(self) -> None:
        """Forward compatibility: an unknown key must not break the task."""
        assert (
            get_retry_policy({"retry": {"max_attempts": 2, "strategy": "linear"}}).max_attempts == 2
        )

    def test_a_numeric_string_is_coerced(self) -> None:
        """JSON payloads from the API may carry numbers as strings."""
        assert get_retry_policy({"retry": {"max_attempts": "4"}}).max_attempts == 4

    def test_an_invalid_value_is_rejected_at_read_time(self) -> None:
        """The policy is validated when read, not silently clamped."""
        with pytest.raises(InvariantViolationError):
            get_retry_policy({"retry": {"max_attempts": 0}})


class TestCalculateBackoff:
    """Exponential growth, a hard cap, and bounded jitter."""

    @pytest.mark.parametrize(
        ("attempt", "expected"),
        [(1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (5, 16.0), (6, 32.0)],
    )
    def test_growth_doubles_per_attempt(self, attempt: int, expected: float) -> None:
        """The first retry waits ``base``; each subsequent one doubles."""
        assert calculate_backoff(attempt=attempt, policy=DETERMINISTIC) == expected

    def test_the_delay_is_capped(self) -> None:
        """Exponential growth must not run away; the cap is the operator's ceiling."""
        policy = RetryPolicy(base_seconds=1.0, max_seconds=10.0, jitter=False)

        assert calculate_backoff(attempt=4, policy=policy) == 8.0
        assert calculate_backoff(attempt=5, policy=policy) == 10.0
        assert calculate_backoff(attempt=100, policy=policy) == 10.0

    def test_a_zero_base_means_retry_immediately(self) -> None:
        """With no base delay the task is retried on the next pass."""
        policy = RetryPolicy(base_seconds=0.0, max_seconds=0.0, jitter=False)

        assert calculate_backoff(attempt=1, policy=policy) == 0.0
        assert calculate_backoff(attempt=10, policy=policy) == 0.0

    @pytest.mark.parametrize("attempt", [0, -1, -100])
    def test_a_non_positive_attempt_is_refused(self, attempt: int) -> None:
        """Attempt numbers are 1-based; zero is a caller bug, not a fast retry."""
        with pytest.raises(InvariantViolationError, match="attempt"):
            calculate_backoff(attempt=attempt, policy=DETERMINISTIC)

    def test_jitter_stays_within_the_raw_delay(self) -> None:
        """Full jitter is ``uniform(0, raw)``: never negative, never above the cap."""
        policy = RetryPolicy(base_seconds=1.0, max_seconds=8.0, jitter=True)
        rng = random.Random(1234)

        samples = [calculate_backoff(attempt=3, policy=policy, rng=rng) for _ in range(500)]

        # attempt 3 -> raw 4.0
        assert all(0.0 <= value <= 4.0 for value in samples)
        # It must actually spread out, not collapse to a constant.
        assert min(samples) < 1.0
        assert max(samples) > 3.0

    def test_jitter_is_deterministic_for_a_seeded_rng(self) -> None:
        """A seeded RNG makes the backoff reproducible, which is what tests need."""
        policy = RetryPolicy(jitter=True)

        first = calculate_backoff(attempt=2, policy=policy, rng=random.Random(42))
        second = calculate_backoff(attempt=2, policy=policy, rng=random.Random(42))

        assert first == second

    def test_jitter_never_exceeds_the_cap(self) -> None:
        """The cap applies to the raw delay, so jitter can only reduce it."""
        policy = RetryPolicy(base_seconds=1.0, max_seconds=5.0, jitter=True)
        rng = random.Random(7)

        assert all(calculate_backoff(attempt=20, policy=policy, rng=rng) <= 5.0 for _ in range(100))
