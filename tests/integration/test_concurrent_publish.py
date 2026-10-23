"""Publishing the same definition from many callers at once.

What is being protected here is stronger than "publish is atomic". A publish
that is atomic can still leave the database holding *someone's* definition when
two callers disagreed about what that definition was. The guarantee that matters
for traceability is narrower:

    Under any interleaving, an ``(organization, name, version)`` ends up holding
    exactly one caller's content — never a mixture of two, and never neither.

That is what makes "etl v3" mean something six months later, when the definition
that produced a run has to be reconstructed exactly. Uniqueness is per
organization (ADR-014), so the race is run inside one tenant.

Two races are covered:

* **Identical content.** The benign case. The database must end up with one
  workflow and its tasks, and every caller that got an answer must have been
  shown the same row.
* **Different content.** The case that would quietly destroy traceability. The
  database must end up holding one caller's definition in full — not a workflow
  from one caller with tasks from another.

Unlike the transition races in ``test_concurrent_transitions.py``, losers here
*are* expected to raise: a ``select_for_update()`` cannot lock a row that does
not exist yet, so on PostgreSQL two concurrent first-publishers both see "not
there" and both try to insert. The unique constraint on ``(organization, name,
version)`` picks the winner and the loser's transaction — workflow and tasks
together — rolls back. Which exception the loser sees depends on the interleaving, so both
acceptable outcomes are enumerated explicitly and anything else fails.

See ``tests/integration/conftest.py`` for the barrier, the per-thread connection
cleanup and the no-stragglers check.
"""

from __future__ import annotations

import pytest
from core.domain.definition import TaskDefinition, WorkflowDefinition
from core.models import Organization, Task, Workflow
from core.repositories.definitions import (
    DefinitionConflictError,
    definition_exists,
    load_definition,
    publish_definition,
)
from django.db import IntegrityError

from tests.integration.conftest import THREADS, require_postgres, run_concurrently_with_losers

pytestmark = [
    pytest.mark.integration,
    pytest.mark.concurrency,
    pytest.mark.django_db(transaction=True),
]

#: The only outcomes a losing publisher may have. A unique-constraint
#: ``IntegrityError`` means the winner committed while this caller was inside
#: its transaction; a ``DefinitionConflictError`` means this caller read the
#: winner's row after it committed. Both leave the database correct.
#:
#: ``OperationalError`` is deliberately absent: PostgreSQL raises one on
#: ``deadlock detected``, and a deadlock in a publish path is a defect, not a
#: race outcome to be tolerated.
ACCEPTED_LOSSES = (DefinitionConflictError, IntegrityError)


def _identical() -> WorkflowDefinition:
    """The definition every caller in the identical-content race publishes."""
    return WorkflowDefinition(
        name="etl",
        version=1,
        tasks=(
            TaskDefinition("extract", "http"),
            TaskDefinition("load", "sql", depends_on=("extract",)),
        ),
    )


def _organization() -> Organization:
    """The tenant every caller in a race publishes into."""
    return Organization.objects.create(name="Acme", slug="acme")


def _candidate(index: int) -> WorkflowDefinition:
    """A one-task definition that is traceable back to its caller.

    The task key carries the index, so the surviving rows can be matched to the
    caller that published them — which is what makes a "mixture" detectable.
    """
    return WorkflowDefinition(
        name="etl",
        version=1,
        tasks=(TaskDefinition(f"only-{index}", "http"),),
    )


def test_publishing_identical_content_concurrently_yields_one_workflow() -> None:
    """Every caller is shown the same row, and only one row exists."""
    require_postgres()

    organization = _organization()
    definitions, losers = run_concurrently_with_losers(
        THREADS,
        lambda _index: publish_definition(_identical(), organization=organization),
    )

    # Nobody may fail for a reason other than losing the race.
    for error in losers:
        assert isinstance(error, ACCEPTED_LOSSES), f"unexpected publisher failure: {error!r}"

    # At least one caller must have published: a race where everybody lost
    # would mean the definition was silently dropped.
    assert definitions, "no caller published anything"

    # Callers that succeeded must have been shown the same row. Two different
    # rows would mean the idempotency check let a duplicate through.
    assert len({workflow.pk for workflow, _created in definitions}) == 1, (
        "successful publishers disagree about which row holds the definition"
    )

    # Exactly one caller may report having *created* the row. Everyone else was
    # either shown the existing row or lost the unique constraint. Two creators
    # would mean a duplicate insert that the constraint somehow allowed.
    creators = [workflow for workflow, created in definitions if created]
    assert len(creators) == 1, f"expected exactly one creator, got {len(creators)}"

    # Exactly one workflow, and both of its tasks. Not two workflows, not a
    # workflow with one task because a second caller's write was half-applied.
    assert Workflow.objects.count() == 1
    assert Task.objects.count() == 2
    assert Task.objects.filter(workflow__name="etl").count() == 2

    stored = load_definition(Workflow.objects.get())
    assert stored == _identical()
    assert stored.fingerprint() == _identical().fingerprint()


def test_publishing_different_content_concurrently_holds_one_definition() -> None:
    """A ``(name, version)`` means exactly one caller's content, under a race."""
    require_postgres()

    organization = _organization()
    publishers, losers = run_concurrently_with_losers(
        THREADS,
        lambda index: publish_definition(_candidate(index), organization=organization),
    )

    for error in losers:
        assert isinstance(error, ACCEPTED_LOSSES), f"unexpected publisher failure: {error!r}"

    # One caller's definition survives, in full.
    assert Workflow.objects.count() == 1
    assert Task.objects.count() == 1

    stored_keys = set(Task.objects.values_list("key", flat=True))
    assert len(stored_keys) == 1, f"expected one task, found {stored_keys}"

    # The surviving key belongs to a real candidate, not to something invented.
    candidates = {f"only-{index}" for index in range(THREADS)}
    assert stored_keys <= candidates, f"found a task key no caller published: {stored_keys}"

    # If a caller was told it published, that caller's content is what is
    # stored. A mismatch here is the traceability failure: the database holds a
    # different definition than the one the caller believes it owns.
    assert len(publishers) <= 1, f"more than one caller believed it published: {publishers}"
    if publishers:
        winner, created = publishers[0]
        assert created is True, "the only caller shown a result must be the one that created it"
        assert load_definition(winner).task_keys == tuple(stored_keys), (
            "the caller that was shown a successful publish does not own the stored rows"
        )


def test_definition_exists_is_true_for_the_different_content_race() -> None:
    """The cheap existence check agrees with what the race actually stored."""
    require_postgres()

    organization = _organization()
    run_concurrently_with_losers(
        THREADS,
        lambda index: publish_definition(_candidate(index), organization=organization),
    )

    assert definition_exists(name="etl", version=1, organization=organization) is True
    assert definition_exists(name="etl", version=2, organization=organization) is False
    assert Workflow.objects.count() == 1
    assert Task.objects.count() == 1
