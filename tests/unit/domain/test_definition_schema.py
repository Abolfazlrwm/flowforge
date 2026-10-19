"""Tests for the JSON payload schema and the parser.

The division being tested: a *shape* problem is reported with a JSONPath, and a
*graph* problem is reported as a domain error naming a task. A payload that is
both malformed and cyclic must produce the shape error, because fixing the shape
is the cheaper of the two for the author to act on.
"""

from __future__ import annotations

import json

import pytest
from core.domain.definition import TaskDefinition, WorkflowDefinition
from core.domain.errors import (
    CyclicDependencyError,
    DuplicateTaskKeyError,
    UnknownDependencyError,
)
from core.domain.schema import (
    WORKFLOW_DEFINITION_SCHEMA,
    DefinitionSchemaError,
    parse_workflow_definition,
    parse_workflow_definition_json,
    to_payload,
    validate_payload,
)
from jsonschema import Draft202012Validator


def _payload() -> dict:
    """A minimal valid payload, to be mutated by individual tests."""
    return {
        "name": "etl",
        "version": 1,
        "tasks": [
            {"key": "extract", "task_type": "http"},
            {"key": "load", "task_type": "sql", "depends_on": ["extract"]},
        ],
    }


class TestHappyPath:
    """Well-formed payloads parse, and the shape the parser emits is legal."""

    def test_a_minimal_payload_parses(self) -> None:
        """The smallest legal payload: name, version, one task."""
        definition = parse_workflow_definition(_payload())
        assert definition.name == "etl"
        assert definition.version == 1
        assert definition.task_keys == ("extract", "load")
        assert definition.task("load").depends_on == ("extract",)

    def test_optional_fields_default(self) -> None:
        """Only ``key`` and ``task_type`` are required per task."""
        definition = parse_workflow_definition(_payload())
        assert definition.description == ""
        assert dict(definition.task("extract").config) == {}

    def test_a_full_payload_parses(self) -> None:
        """Every optional field present, to prove none of them is silently dropped."""
        payload = {
            "name": "etl",
            "version": 3,
            "description": "nightly etl",
            "tasks": [
                {
                    "key": "extract",
                    "task_type": "http",
                    "config": {"url": "https://example.test", "retries": 3},
                },
                {"key": "load", "task_type": "sql", "depends_on": ["extract"], "config": {}},
            ],
        }
        definition = parse_workflow_definition(payload)
        assert definition.version == 3
        assert definition.description == "nightly etl"
        assert dict(definition.task("extract").config) == {
            "url": "https://example.test",
            "retries": 3,
        }

    def test_a_json_string_parses(self) -> None:
        """The convenience entry point the API layer actually calls."""
        definition = parse_workflow_definition_json(json.dumps(_payload()))
        assert definition.task_keys == ("extract", "load")

    def test_a_non_json_string_reports_the_json_error(self) -> None:
        """A broken body must raise the domain error, not a bare ``JSONDecodeError``."""
        with pytest.raises(DefinitionSchemaError, match="not valid JSON"):
            parse_workflow_definition_json("{not json")


class TestShapeErrors:
    """Every malformed payload produces a JSONPath, not a Python traceback."""

    def test_a_payload_that_is_not_an_object(self) -> None:
        """A list where a mapping is expected: ``$`` is the whole payload."""
        with pytest.raises(DefinitionSchemaError, match=r"\$:"):
            validate_payload([1, 2, 3])

    def test_a_missing_required_field(self) -> None:
        """The path is ``$``, not ``$.name``.

        JSON Schema reports a missing required property against the *object*, so
        the JSONPath cannot name the field that is absent — only the message
        does. Asserting ``$.name`` here would be asserting something the schema
        never promises.
        """
        payload = _payload()
        del payload["name"]
        with pytest.raises(DefinitionSchemaError, match=r"\$: 'name' is a required property"):
            validate_payload(payload)

    def test_a_wrong_field_type(self) -> None:
        """``version`` as a string — what an untyped JSON client sends."""
        payload = _payload()
        payload["version"] = "one"
        with pytest.raises(DefinitionSchemaError, match=r"\$\.version.*not of type 'integer'"):
            validate_payload(payload)

    def test_an_empty_task_list(self) -> None:
        """A workflow with no tasks would schedule and do nothing, silently."""
        payload = _payload()
        payload["tasks"] = []
        with pytest.raises(DefinitionSchemaError, match=r"\$\.tasks.*non-empty"):
            validate_payload(payload)

    def test_an_unknown_top_level_field(self) -> None:
        """``additionalProperties: false`` — a typo must not be silently ignored.

        A silently-ignored field means a payload that looks like it sets a
        timeout does nothing at all.
        """
        payload = _payload()
        payload["timeout"] = 30
        with pytest.raises(DefinitionSchemaError, match="Additional properties"):
            validate_payload(payload)

    def test_an_unknown_task_field(self) -> None:
        """A typo in a task field is named with the array index, not just ``$.tasks``."""
        payload = _payload()
        payload["tasks"][0]["configg"] = {}
        with pytest.raises(DefinitionSchemaError, match=r"\$\.tasks\[0\]"):
            validate_payload(payload)

    def test_a_task_missing_a_key(self) -> None:
        """The path is the item, not the property inside it.

        Same rule as the top-level case: a missing property is reported against
        the object that should have held it.
        """
        payload = _payload()
        del payload["tasks"][0]["task_type"]
        with pytest.raises(DefinitionSchemaError, match=r"\$\.tasks\[0\].*task_type.*required"):
            validate_payload(payload)

    def test_a_task_missing_depends_on_is_allowed(self) -> None:
        """``depends_on`` and ``config`` are optional and default to empty."""
        assert parse_workflow_definition(_payload()).task("extract").depends_on == ()

    def test_a_version_below_one(self) -> None:
        """Zero is a valid integer but not a valid version: it is not > ``unpublished``."""
        payload = _payload()
        payload["version"] = 0
        with pytest.raises(DefinitionSchemaError, match=r"\$\.version.*minimum"):
            validate_payload(payload)

    def test_a_duplicate_task_key_is_refused_by_the_graph(self) -> None:
        """Duplicate keys pass the shape check and are refused by the domain.

        JSON Schema's ``uniqueItems`` cannot compare across separate objects,
        so the payload is well-formed. The domain error names the offending key,
        which the schema message would not — that is why it is not folded into
        the schema as a ``oneOf`` trick.
        """
        payload = _payload()
        payload["tasks"].append({"key": "extract", "task_type": "http"})
        # Well-formed...
        validate_payload(payload)
        # ...but not usable.
        with pytest.raises(DuplicateTaskKeyError, match="duplicate task key 'extract'"):
            parse_workflow_definition(payload)

    def test_a_key_with_illegal_characters(self) -> None:
        """Keys appear in URLs and in job names, so they are restricted.

        A key of ``../../etc/passwd`` would be a path traversal if a task name
        ever became a filesystem path.
        """
        payload = _payload()
        payload["tasks"][0]["key"] = "bad key!"
        with pytest.raises(DefinitionSchemaError, match=r"\$\.tasks\[0\]\.key"):
            validate_payload(payload)

    def test_a_dotted_key_is_accepted(self) -> None:
        """The key pattern deliberately permits dots, dashes and underscores."""
        payload = _payload()
        payload["tasks"][0]["key"] = "a.b-c_1"
        payload["tasks"][1]["depends_on"] = ["a.b-c_1"]
        definition = parse_workflow_definition(payload)
        assert definition.task("a.b-c_1").key == "a.b-c_1"
        assert definition.graph.dependencies_of("load") == {"a.b-c_1"}

    def test_all_problems_are_reported_together(self) -> None:
        """One round trip fixes one field, not one field per request."""
        payload = {"name": "", "version": 0, "tasks": []}
        with pytest.raises(DefinitionSchemaError) as raised:
            validate_payload(payload)
        assert len(raised.value.problems) >= 3

    def test_problems_are_deduplicated(self) -> None:
        """JSON Schema reports nested and parent failures for the same field."""
        payload = _payload()
        payload["tasks"][0] = "not-an-object"
        with pytest.raises(DefinitionSchemaError) as raised:
            validate_payload(payload)
        assert len(raised.value.problems) == len(set(raised.value.problems))


class TestGraphErrors:
    """A well-formed payload whose graph cannot run."""

    def test_an_unknown_dependency(self) -> None:
        """The error names the dangling reference, so the author knows which task."""
        payload = _payload()
        payload["tasks"][1]["depends_on"] = ["ghost"]
        with pytest.raises(UnknownDependencyError, match="depends on unknown task 'ghost'"):
            parse_workflow_definition(payload)

    def test_a_cycle(self) -> None:
        """Two tasks each waiting on the other: neither can ever start."""
        payload = {
            "name": "c",
            "version": 1,
            "tasks": [
                {"key": "a", "task_type": "t", "depends_on": ["b"]},
                {"key": "b", "task_type": "t", "depends_on": ["a"]},
            ],
        }
        with pytest.raises(CyclicDependencyError):
            parse_workflow_definition(payload)

    def test_a_self_dependency(self) -> None:
        """A one-node cycle: the degenerate case the search must still catch."""
        payload = {
            "name": "c",
            "version": 1,
            "tasks": [{"key": "a", "task_type": "t", "depends_on": ["a"]}],
        }
        with pytest.raises(CyclicDependencyError):
            parse_workflow_definition(payload)

    def test_a_shape_error_precedes_a_graph_error(self) -> None:
        """Both wrong: the shape error wins, because that is the cheaper fix."""
        payload = {
            "name": "c",
            # not an integer, so the shape check fails before the graph is built
            "version": "one",
            "tasks": [
                {"key": "a", "task_type": "t", "depends_on": ["a"]},
            ],
        }
        with pytest.raises(DefinitionSchemaError):
            parse_workflow_definition(payload)

    def test_parse_validates_the_graph_eagerly(self) -> None:
        """The bug this file guards against.

        ``WorkflowDefinition.graph`` is a cached property, so a parser that
        merely constructed the definition would accept a cyclic payload and only
        fail when something later touched the graph — inside a transaction, on a
        machine that did not write the definition.
        """
        payload = {
            "name": "c",
            "version": 1,
            "tasks": [
                {"key": "a", "task_type": "t", "depends_on": ["b"]},
                {"key": "b", "task_type": "t", "depends_on": ["a"]},
            ],
        }
        with pytest.raises(CyclicDependencyError):
            parse_workflow_definition(payload)


class TestSerialisation:
    """to_payload must round-trip, so a definition is exportable and re-importable."""

    def test_round_trip(self) -> None:
        """Export then re-import yields an equal value object, not a similar one."""
        original = parse_workflow_definition(_payload())
        again = parse_workflow_definition(to_payload(original))
        assert original == again

    def test_the_fingerprint_survives_a_round_trip(self) -> None:
        """A definition's identity must not depend on how it was serialised."""
        original = parse_workflow_definition(_payload())
        assert (
            original.fingerprint() == parse_workflow_definition(to_payload(original)).fingerprint()
        )

    def test_payload_shape_matches_the_schema(self) -> None:
        """Guards ``to_payload`` against drifting away from the schema it claims."""
        validator = Draft202012Validator(WORKFLOW_DEFINITION_SCHEMA)
        definition = WorkflowDefinition(
            name="n",
            version=1,
            tasks=(TaskDefinition("a", "t", depends_on=()),),
        )
        errors = list(validator.iter_errors(to_payload(definition)))
        assert errors == [], f"{errors[0].json_path}: {errors[0].message}" if errors else ""

    def test_payload_is_json_serialisable(self) -> None:
        """The config mapping is a MappingProxyType, which ``json`` cannot encode."""
        definition = parse_workflow_definition(_payload())
        assert json.loads(json.dumps(to_payload(definition))) == to_payload(definition)
