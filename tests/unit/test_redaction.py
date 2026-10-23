"""Secret redaction: what counts as a secret, and how it is replaced."""

from __future__ import annotations

from core.security import REDACTED, is_secret_key, redact_mapping


class TestIsSecretKey:
    """The key-name heuristic, which is deliberately broad."""

    def test_common_secret_names_are_secrets(self) -> None:
        """The names a task is most likely to use."""
        for key in ("password", "passphrase", "secret", "token", "api_key", "apiKey"):
            assert is_secret_key(key), key

    def test_a_non_secret_name_is_not(self) -> None:
        """A false positive hides a harmless value, but this is plainly not one."""
        assert not is_secret_key("url")
        assert not is_secret_key("count")


class TestRedactMapping:
    """Redaction is recursive and never mutates the input."""

    def test_a_secret_key_is_redacted(self) -> None:
        """The direct case."""
        assert redact_mapping({"password": "hunter2"}) == {"password": REDACTED}

    def test_nested_secrets_are_redacted(self) -> None:
        """A secret buried in a nested mapping is still a secret."""
        data = {"db": {"host": "localhost", "password": "hunter2"}}

        assert redact_mapping(data) == {"db": {"host": "localhost", "password": REDACTED}}

    def test_a_secret_list_is_redacted(self) -> None:
        """Lists are traversed too."""
        assert redact_mapping({"items": [{"token": "a"}, {"token": "b"}]}) == {
            "items": [{"token": REDACTED}, {"token": REDACTED}]
        }

    def test_the_input_is_not_mutated(self) -> None:
        """The worker keeps using the real value while the log gets the redacted one."""
        data = {"password": "hunter2"}

        redact_mapping(data)

        assert data == {"password": "hunter2"}

    def test_a_mapping_marked_secret_is_wholly_redacted(self) -> None:
        """``secret: true`` hides every value, keeping the marker."""
        data = {"secret": True, "value": "hello", "count": 3}

        assert redact_mapping(data) == {"secret": True, "value": REDACTED, "count": REDACTED}

    def test_extra_secret_keys_are_honoured(self) -> None:
        """A task may name its own secret field."""
        assert redact_mapping({"pin": "1234"}, secret_keys={"pin"}) == {"pin": REDACTED}

    def test_scalars_pass_through(self) -> None:
        """A bare string is not a mapping and is returned unchanged."""
        assert redact_mapping("hello") == "hello"
