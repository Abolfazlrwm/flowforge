"""Retry policy and backoff, pure domain logic (no Django imports).

A task's retry behaviour is described by a :class:`RetryPolicy`, read from the
task's ``config["retry"]`` block by :func:`get_retry_policy`. Keeping this in the
domain layer (zero Django) means the policy and its backoff math are testable
with plain values, and the scheduler/reaper can import it without dragging in the
ORM.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.domain.errors import InvariantViolationError


@dataclass(frozen=True)
class RetryPolicy:
    """How many times, and how slowly, a failing task may be retried.

    Attributes:
        max_attempts: Total attempts allowed (first attempt + retries). Must be >= 1.
        base_seconds: The delay before the *second* attempt; the first retry waits
            ``base_seconds``, the next ``base_seconds * 2``, and so on.
        max_seconds: Upper bound on the delay; the exponential growth is capped here.
        jitter: When true, the delay is multiplied by a uniform random factor in
            ``[0, 1]`` (full jitter) so many failing tasks do not all retry in
            lockstep and stampede the broker.
    """

    max_attempts: int = 3
    base_seconds: float = 1.0
    max_seconds: float = 60.0
    jitter: bool = True

    def __post_init__(self) -> None:
        """Validate the policy values."""
        if self.max_attempts < 1:
            raise InvariantViolationError(f"max_attempts must be >= 1, got {self.max_attempts}")
        if self.base_seconds < 0:
            raise InvariantViolationError(f"base_seconds must be >= 0, got {self.base_seconds}")
        if self.max_seconds < self.base_seconds:
            raise InvariantViolationError(
                f"max_seconds ({self.max_seconds}) must be >= base_seconds ({self.base_seconds})"
            )


#: Used when a task definition says nothing about retries.
DEFAULT_RETRY_POLICY = RetryPolicy()

#: Backoff for *connection* retries, used by the worker and the publisher when the
#: broker goes away. The arithmetic is the same exponential-with-jitter curve as a
#: task retry, applied to reconnecting rather than re-executing, so it reuses
#: :func:`calculate_backoff` instead of growing a second implementation.
#:
#: ``max_attempts`` is meaningless here (a connection retries forever until the
#: process is told to stop) and is set to 1 so the dataclass invariant holds.
CONNECTION_BACKOFF = RetryPolicy(max_attempts=1, base_seconds=0.5, max_seconds=30.0, jitter=True)


def get_retry_policy(config: Mapping[str, Any] | None) -> RetryPolicy:
    """Read the retry policy out of a task's ``config`` mapping.

    The domain layer stays Django-free, so this takes the config dict directly
    rather than a ``Task`` model. A missing or empty ``retry`` block yields the
    :data:`DEFAULT_RETRY_POLICY`. Unknown keys are ignored; only ``max_attempts``,
    ``base_seconds``, ``max_seconds`` and ``jitter`` are read, and each is
    re-validated by :class:`RetryPolicy`.
    """
    if not config:
        return DEFAULT_RETRY_POLICY
    retry = config.get("retry")
    if not isinstance(retry, Mapping):
        return DEFAULT_RETRY_POLICY
    return RetryPolicy(
        max_attempts=int(retry.get("max_attempts", DEFAULT_RETRY_POLICY.max_attempts)),
        base_seconds=float(retry.get("base_seconds", DEFAULT_RETRY_POLICY.base_seconds)),
        max_seconds=float(retry.get("max_seconds", DEFAULT_RETRY_POLICY.max_seconds)),
        jitter=bool(retry.get("jitter", DEFAULT_RETRY_POLICY.jitter)),
    )


def calculate_backoff(
    *,
    attempt: int,
    policy: RetryPolicy,
    rng: random.Random | None = None,
) -> float:
    """Seconds to wait before the given attempt may start.

    ``attempt`` is the attempt number about to begin (1-based). The delay is
    ``min(base * 2**(attempt-1), max)`` — exponential growth capped at ``max`` — and,
    with jitter enabled, multiplied by a uniform random factor in ``[0, 1]`` so
    failing tasks do not all retry simultaneously.

    Raises:
        InvariantViolationError: if ``attempt`` is not positive.
    """
    if attempt < 1:
        raise InvariantViolationError(f"attempt must be >= 1, got {attempt}")
    raw: float = min(policy.base_seconds * (2 ** (attempt - 1)), policy.max_seconds)
    if not policy.jitter:
        return raw
    # Jitter only needs to be unpredictable enough to spread retries out; it is
    # not a security control, so a non-cryptographic PRNG is the right tool.
    rng = rng or random.Random()  # noqa: S311  # nosec B311 - jitter, not a security control
    return rng.uniform(0.0, raw)
