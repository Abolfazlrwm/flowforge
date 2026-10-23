"""Redaction of secret-marked data before it reaches a log, event or response.

A task's ``config`` may legitimately carry a password, an API token or a private
key. The worker needs those values to execute the task, so the dispatch payload
is *not* redacted — but nothing a human or a client reads should contain them.
This module is the single definition of "what counts as a secret" and how it is
replaced, so a log line, an audit entry and an API response cannot disagree.

Two mechanisms mark a value as secret:

1. **By key name.** A key matching ``password``, ``secret``, ``token``,
   ``api_key``, ``authorization``, ``credential`` or ``private_key`` is redacted
   wherever it appears, at any depth.
2. **By declaration.** A mapping containing ``"secret": true`` is treated as
   wholly secret: every value in it is redacted, and the marker itself is kept so
   a reader can see why.
"""

from __future__ import annotations

import re
from typing import Any

#: The replacement written in place of a secret value.
REDACTED = "***REDACTED***"

#: A key name that denotes a secret. Deliberately broad: a false positive hides a
#: harmless value, while a false negative leaks a credential.
_SECRET_KEY = re.compile(
    r"(pass(word|phrase)?|secret|token|api[_-]?key|authorization|credential|"
    r"private[_-]?key|client[_-]?secret|access[_-]?key)",
    re.IGNORECASE,
)


def is_secret_key(key: str) -> bool:
    """Whether a mapping key names a secret."""
    return bool(_SECRET_KEY.search(key))


def redact_mapping(data: Any, *, secret_keys: frozenset[str] | set[str] = frozenset()) -> Any:
    """Return a copy of ``data`` with secret values replaced by :data:`REDACTED`.

    ``secret_keys`` names additional keys to redact (for a task that declares its
    own secret field names). The input is never mutated: a caller may keep using
    the original for execution while logging the redacted copy.
    """
    return _redact(data, frozenset(secret_keys))


def _redact(value: Any, secret_keys: frozenset[str]) -> Any:
    """Recursively redact ``value``."""
    if isinstance(value, dict):
        whole_mapping_is_secret = value.get("secret") is True
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if key == "secret":
                redacted[key] = item
            elif whole_mapping_is_secret or is_secret_key(str(key)) or str(key) in secret_keys:
                redacted[key] = REDACTED
            else:
                redacted[key] = _redact(item, secret_keys)
        return redacted
    if isinstance(value, list):
        return [_redact(item, secret_keys) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item, secret_keys) for item in value)
    return value
