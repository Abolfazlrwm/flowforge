"""The JSON payload schema for workflow definitions, and the parser.

Two different jobs are deliberately kept apart:

* **Shape** — is this a well-formed definition? Done by a JSON Schema
  (`jsonschema`, which is already a declared dependency) and reported with a
  JSONPath, so an API caller is told *where* the problem is. JSON Schema cannot
  express "``depends_on`` must reference a task that exists" or "no cycles", so
  it stops at types and required fields.
* **Graph** — is this definition internally consistent? Done by
  :class:`~core.domain.definition.DependencyGraph`, which raises domain errors.

Merging the two into one hand-written validator would mean every field gets two
sources of truth and the error messages lose their paths. Keeping them apart
means a typo'd payload and a broken reference produce different, both-useful
errors.
"""

from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from core.domain.definition import TaskDefinition, WorkflowDefinition
from core.domain.errors import DomainError

#: The shape of a single task in a definition payload.
TASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["key", "task_type"],
    "additionalProperties": False,
    "properties": {
        "key": {
            "type": "string",
            "minLength": 1,
            "pattern": r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
            "description": "Stable identifier, referenced by other tasks' depends_on.",
        },
        "task_type": {
            "type": "string",
            "minLength": 1,
            "description": "Names the handler registered for this task (Phase 5).",
        },
        "depends_on": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
            "default": [],
            "description": "Keys of tasks that must succeed first.",
        },
        "config": {
            "type": "object",
            "default": {},
            "description": "Free-form parameters handed to the handler.",
        },
    },
}

#: The shape of a whole workflow definition payload.
WORKFLOW_DEFINITION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "FlowForge workflow definition",
    "type": "object",
    "required": ["name", "version", "tasks"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 200},
        "version": {"type": "integer", "minimum": 1},
        "description": {"type": "string", "default": "", "maxLength": 2000},
        "tasks": {
            "type": "array",
            "minItems": 1,
            "items": TASK_SCHEMA,
            "description": "The tasks of this definition. Order is presentation only.",
        },
    },
}

_VALIDATOR = Draft202012Validator(WORKFLOW_DEFINITION_SCHEMA)


class DefinitionSchemaError(DomainError):
    """The payload is not a valid FlowForge definition.

    Raised for a *shape* violation, and reported with a JSONPath so the caller
    is told which field is wrong rather than that the document as a whole is
    wrong. A `SchemaError` from the validator would mean the schema itself is
    broken, which is a defect rather than a caller mistake; that is surfaced
    separately so the two can never be confused.
    """

    def __init__(self, problems: tuple[str, ...]) -> None:
        self.problems = problems
        joined = "\n".join(f"  - {problem}" for problem in problems)
        super().__init__(f"{len(problems)} problem(s) in workflow definition:\n{joined}")


def _worst_first(problems: list[str]) -> tuple[str, ...]:
    """Deduplicate and order problems so the most specific comes first.

    JSON Schema reports both "the object is not valid" and, separately, each
    property-level failure inside it. Sorting by path length means the caller
    sees ``$.tasks[1].key`` rather than ``$`` first — the deep error is the one
    they can act on.
    """
    return tuple(sorted(dict.fromkeys(problems), key=lambda problem: (-len(problem), problem)))


def validate_payload(payload: Any) -> None:
    """Validate a parsed definition payload against the JSON Schema.

    A non-mapping payload is reported by the schema itself, with ``$`` as its
    path, so there is one error format for every shape problem rather than a
    special case decided in Python.

    Raises:
        DefinitionSchemaError: listing every problem found, in
            most-specific-first order.
    """
    try:
        errors = sorted(
            _VALIDATOR.iter_errors(payload), key=lambda error: list(error.absolute_path)
        )
    except SchemaError as error:
        raise TypeError(f"workflow definition schema is invalid: {error.message}") from error

    if errors:
        raise DefinitionSchemaError(
            _worst_first([f"{error.json_path}: {error.message}" for error in errors])
        )


def parse_workflow_definition(payload: Any) -> WorkflowDefinition:
    """Parse and fully validate a definition payload.

    Runs the shape check first, then builds the dependency graph. A graph error
    names a specific task and a specific problem; a shape error names a JSONPath.
    Neither is ever reported as a database error.

    The explicit ``.graph`` access is load-bearing. The graph is a
    `functools.cached_property`, so without forcing it here a definition with a
    dependency cycle or an unknown reference would be constructed successfully
    and only fail later — during scheduling, inside a transaction, on a machine
    that did not write the definition. This line is what makes
    "parse" mean "validated".

    Raises:
        DefinitionSchemaError: if the payload is malformed.
        DefinitionError: if the payload is well-formed but internally
            inconsistent (unknown dependency, duplicate key, cycle).
    """
    validate_payload(payload)
    definition = WorkflowDefinition(
        name=payload["name"],
        version=payload["version"],
        description=payload.get("description", ""),
        tasks=tuple(
            TaskDefinition(
                key=task["key"],
                task_type=task["task_type"],
                depends_on=tuple(task.get("depends_on", ())),
                config=dict(task.get("config", {})),
            )
            for task in payload["tasks"]
        ),
    )
    # Force the cached graph now, while we can still attribute the failure to
    # whoever submitted the definition.
    definition.graph  # noqa: B018 - forcing the cached property is the point
    return definition


def parse_workflow_definition_json(text: str) -> WorkflowDefinition:
    """Parse a definition from a JSON string.

    Raises:
        json.JSONDecodeError: if the text is not JSON.
        DefinitionSchemaError: if the text is JSON but not a valid definition.
        DefinitionError: if the definition is internally inconsistent.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise DefinitionSchemaError((f"$: not valid JSON — {error}",)) from error
    return parse_workflow_definition(payload)


def to_payload(definition: WorkflowDefinition) -> dict[str, Any]:
    """Render a definition back into its JSON payload shape.

    Round-trips with `parse_workflow_definition`, which is what makes the format
    testable and an exported definition re-importable.
    """
    return {
        "name": definition.name,
        "version": definition.version,
        "description": definition.description,
        "tasks": [
            {
                "key": task.key,
                "task_type": task.task_type,
                "depends_on": list(task.depends_on),
                "config": dict(task.config),
            }
            for task in definition.tasks
        ],
    }
