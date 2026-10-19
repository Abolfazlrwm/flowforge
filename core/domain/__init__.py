"""The pure-Python domain layer.

Nothing in this package may import Django, DRF, Celery or a driver. A test in
``tests/unit/domain/test_no_framework_imports.py`` enforces that by parsing the
AST of every module here, and a second test proves it at runtime by importing
the package with ``django`` made unimportable.

Keeping the rules framework-free is what lets the same transition table be used
by a web request, a worker and a test with no divergence between them.
"""

from __future__ import annotations

from core.domain.definition import DependencyGraph, TaskDefinition, WorkflowDefinition
from core.domain.entities import Task, TaskRun, Workflow, WorkflowRun
from core.domain.errors import (
    CyclicDependencyError,
    DefinitionError,
    DomainError,
    DuplicateTaskKeyError,
    IllegalTransitionError,
    InvariantViolationError,
    LeaseRequiredError,
    UnknownDependencyError,
)
from core.domain.machines import MACHINES, TASK_RUN_MACHINE, WORKFLOW_RUN_MACHINE
from core.domain.schema import (
    DefinitionSchemaError,
    parse_workflow_definition,
    to_payload,
    validate_payload,
)
from core.domain.services import derive_workflow_run_state, initial_task_run_state
from core.domain.states import TaskRunState, WorkflowRunState
from core.domain.transitions import StateMachine, Transition

__all__ = [
    "MACHINES",
    "TASK_RUN_MACHINE",
    "WORKFLOW_RUN_MACHINE",
    "CyclicDependencyError",
    "DefinitionError",
    "DefinitionSchemaError",
    "DependencyGraph",
    "DomainError",
    "DuplicateTaskKeyError",
    "IllegalTransitionError",
    "InvariantViolationError",
    "LeaseRequiredError",
    "StateMachine",
    "Task",
    "TaskDefinition",
    "TaskRun",
    "TaskRunState",
    "Transition",
    "UnknownDependencyError",
    "Workflow",
    "WorkflowDefinition",
    "WorkflowRun",
    "WorkflowRunState",
    "derive_workflow_run_state",
    "initial_task_run_state",
    "parse_workflow_definition",
    "to_payload",
    "validate_payload",
]
