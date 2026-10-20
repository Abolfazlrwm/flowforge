"""Publishing and loading workflow definitions.

This is the only place a definition crosses from JSON-shaped data into rows. The
round trip is lossless in both directions, which is what makes a run traceable:
whatever was published is what comes back out.

Two properties are enforced here rather than trusted to callers:

1. **Publish is atomic.** A `Workflow` and all its `Task` rows appear together,
   so a definition that fails halfway never leaves a workflow with tasks it
   cannot schedule.
2. **Publish is idempotent by content.** Reposting the identical definition
   returns the existing rows. Posting *different* content under a `(name,
   version)` that already exists is refused, because a run must be able to trace
   itself back to exactly the definition it executed.
"""

from __future__ import annotations

from django.db import transaction

from core.domain.definition import TaskDefinition, WorkflowDefinition
from core.domain.errors import DefinitionError
from core.models import Task, Workflow

__all__ = [
    "DefinitionConflictError",
    "definition_exists",
    "load_definition",
    "publish_definition",
]


class DefinitionConflictError(Exception):
    """A definition was published under a `(name, version)` that already exists.

    Only raised when the *content* differs. Republishing an identical definition
    is a no-op, not a conflict — otherwise a retry after a network blip would
    fail on a request that had actually succeeded.
    """

    def __init__(self, workflow: Workflow, published: WorkflowDefinition) -> None:
        self.workflow = workflow
        self.published = published
        super().__init__(
            f"{workflow.name} v{workflow.version} already exists with different content. "
            "Bump the version to publish a change, or repost the identical "
            "definition to confirm it."
        )


def publish_definition(definition: WorkflowDefinition) -> tuple[Workflow, bool]:
    """Create the rows for `definition`, returning the `Workflow` row.

    Returns:
        The workflow, and whether this call created it. `False` means an
        identical definition was already published and this call was a no-op —
        the caller needs to know, because "created" is a 201 and "already there"
        is a 200, and guessing from a pre-check would be a race.

    Idempotent: an existing `(name, version)` with the same fingerprint returns
    the existing row without creating anything.

    Raises:
        DefinitionConflictError: if `(name, version)` exists with different content.
        DefinitionError: if the definition's dependency graph is invalid. Checked
            first, so no rows are written for a definition that cannot run.
        IntegrityError: if two callers race on the same `(name, version)`; the
            unique constraint decides the winner, exactly as in Phase 1.
    """
    # Force the cached graph even though the caller may not have touched it, so
    # validation happens before any row is written rather than during scheduling.
    definition.graph  # noqa: B018 — the expression statement is the assertion

    with transaction.atomic():
        # Lock the row if it exists, so two concurrent publishes of the same
        # (name, version) cannot both see "not there" and both insert.
        existing = (
            Workflow.objects.select_for_update()
            .filter(name=definition.name, version=definition.version)
            .select_related()
            .first()
        )
        if existing is not None:
            if _fingerprint_of(existing) != definition.fingerprint():
                raise DefinitionConflictError(existing, definition)
            return existing, False

        workflow = Workflow.objects.create(
            name=definition.name,
            version=definition.version,
            description=definition.description,
        )
        Task.objects.bulk_create(
            [
                Task(
                    workflow=workflow,
                    key=task.key,
                    task_type=task.task_type,
                    depends_on=sorted(task.depends_on),
                    config=dict(task.config),
                )
                for task in definition.tasks
            ]
        )
        return workflow, True


def load_definition(workflow: Workflow) -> WorkflowDefinition:
    """Reconstruct a definition from its rows.

    The result goes through the same graph validation as a parsed payload, so a
    row set that somehow escaped validation — a manual `UPDATE`, a migration
    that lost data, a bug in an earlier release — is caught on the way out
    instead of surfacing at schedule time inside a transaction.

    The explicit ``.graph`` access is load-bearing for the same reason it is in
    ``parse_workflow_definition``: ``graph`` is a cached property, so without
    forcing it here a broken row set would load successfully and only fail when
    a scheduler later touched the graph.

    Raises:
        DefinitionError: if the stored rows do not form a valid definition,
            including a row whose `task_type` was never populated (possible only
            in a database where the Phase 1 schema held `Task` rows created
            before migration `0002`).
    """
    tasks: list[TaskDefinition] = []
    for task in workflow.tasks.order_by("created_at", "key"):
        if not task.task_type:
            raise DefinitionError(
                f"task {task.key!r} of {workflow.name} v{workflow.version} has no task_type. "
                "This row predates migration core.0002 and was never backfilled."
            )
        tasks.append(
            TaskDefinition(
                key=task.key,
                task_type=task.task_type,
                depends_on=tuple(sorted(task.depends_on)),
                config=task.config or {},
            )
        )
    definition = WorkflowDefinition(
        name=workflow.name,
        version=workflow.version,
        description=workflow.description,
        tasks=tuple(tasks),
    )
    # Force validation before returning, so a broken row set fails here.
    definition.graph  # noqa: B018 - forcing the cached property is the point
    return definition


def definition_exists(*, name: str, version: int) -> bool:
    """Whether a definition with this `(name, version)` has been published."""
    return Workflow.objects.filter(name=name, version=version).exists()


def _fingerprint_of(workflow: Workflow) -> str:
    """Fingerprint of a persistent `Workflow`, comparable to a definition's.

    Uses :func:`load_definition` so both sides of the comparison run through the
    identical code path — a hand-rolled second fingerprint would eventually
    disagree with the first about what "same content" means.
    """
    return load_definition(workflow).fingerprint()
