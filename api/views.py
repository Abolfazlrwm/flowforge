"""Views.

Every view here does the same four things and nothing else: pick a queryset,
declare permissions and throttles, call one service, serialise the result. A
view that builds a definition, decides whether a publish conflicts, or chooses
which tasks to create is a view holding business logic, and the tests for that
logic then live in the wrong place.

Status codes are the contract:

| Situation | Code |
| --- | --- |
| Definition published | 201 |
| Definition already published with identical content | 200 |
| Run triggered | 202 |
| Replayed trigger (same idempotency key) | 202, `Idempotent-Replay: true` |
| Malformed payload, bad graph, unknown filter value | 400 |
| Missing or invalid credentials | 401 |
| Authenticated but not permitted | 403 |
| Unknown definition or run | 404 |
| Key reused with a different body | 422 |
"""

from __future__ import annotations

from typing import Any

from core.domain.schema import parse_workflow_definition
from core.models import TaskRun, TaskRunEvent, Workflow, WorkflowRun
from core.repositories.definitions import publish_definition
from core.repositories.transitions import requeue_failed_task_run
from core.services import runs as run_service
from django.db.models import Count, QuerySet
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from api.filters import WorkflowFilter, WorkflowRunFilter
from api.pagination import StandardCursorPagination
from api.permissions import IsAuthenticatedAndActive, IsOwnerOrStaff, IsStaffOrReadOnly
from api.serializers import (
    TaskRunEventSerializer,
    TaskRunSerializer,
    TriggerSerializer,
    UserSerializer,
    WorkflowDefinitionDoc,
    WorkflowRunSerializer,
    WorkflowRunSummarySerializer,
    WorkflowSerializer,
    WorkflowSummarySerializer,
)

IDEMPOTENCY_HEADER = "Idempotency-Key"


class MeView(APIView):
    """The authenticated caller.

    Useful as a cheap "is this token still good" probe, and as the one endpoint
    whose permission story is trivially inspectable.
    """

    permission_classes = [IsAuthenticatedAndActive]
    throttle_scope = "read"

    @extend_schema(responses={200: UserSerializer}, tags=["auth"])
    def get(self, request: Request) -> Response:
        """Return the caller's own account."""
        return Response(UserSerializer(request.user).data)


class WorkflowListCreateView(ListAPIView[Workflow]):
    """List published definitions, or publish a new one."""

    permission_classes = [IsStaffOrReadOnly]
    pagination_class = StandardCursorPagination
    filterset_class = WorkflowFilter
    ordering = ["-created_at"]

    def get_throttles(self) -> list[Any]:
        """Reads and publishes are throttled at different rates."""
        self.throttle_scope = "read" if self.request.method == "GET" else "trigger"
        return super().get_throttles()

    def get_queryset(self) -> QuerySet[Workflow]:
        """Definitions with their task count, newest first."""
        return Workflow.objects.annotate(task_count=Count("tasks")).order_by("-created_at", "-id")

    def get_serializer_class(self) -> type[WorkflowSummarySerializer]:
        """List only; publishing does not go through a serializer."""
        return WorkflowSummarySerializer

    @extend_schema(
        request=WorkflowDefinitionDoc,
        responses={
            201: WorkflowSerializer,
            200: OpenApiResponse(description="Identical definition already published."),
            400: OpenApiResponse(description="Malformed payload, or an invalid graph."),
        },
        tags=["workflows"],
    )
    def post(self, request: Request) -> Response:
        """Publish a definition.

        The body is validated by the JSON Schema in `core.domain.schema`, not by
        a serializer: that schema is also what the parser and the persistence
        round trip use, and a second validator here would drift from it.
        """
        definition = parse_workflow_definition(request.data)
        workflow, created = publish_definition(definition)
        payload = WorkflowSerializer(_with_tasks(workflow)).data
        return Response(payload, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class WorkflowDetailView(RetrieveAPIView[Workflow]):
    """One published definition, by name and version.

    `(name, version)` is the domain's identity for a definition, so the URL uses
    it rather than the row's UUID. A client that knows which definition it wants
    already knows both values; making it look up an id first would be a second
    round trip for nothing.
    """

    permission_classes = [IsStaffOrReadOnly]
    serializer_class = WorkflowSerializer
    throttle_scope = "read"
    lookup_field = "name"

    def get_queryset(self) -> QuerySet[Workflow]:
        """Definitions with their tasks prefetched, to avoid N+1 on render."""
        return Workflow.objects.prefetch_related("tasks")

    def get_object(self) -> Workflow:
        """Resolve `(name, version)` from the URL."""
        return get_object_or_404(
            self.get_queryset(),
            name=self.kwargs["name"],
            version=self.kwargs["version"],
        )


class RunListCreateView(ListAPIView[WorkflowRun]):
    """List the caller's runs, or trigger a new one."""

    permission_classes = [IsAuthenticatedAndActive]
    pagination_class = StandardCursorPagination
    filterset_class = WorkflowRunFilter
    ordering = ["-created_at"]

    def get_throttles(self) -> list[Any]:
        """Triggering is expensive; listing is not."""
        self.throttle_scope = "trigger" if self.request.method == "POST" else "read"
        return super().get_throttles()

    def get_queryset(self) -> QuerySet[WorkflowRun]:
        """Only the caller's runs, unless they are staff."""
        return run_service.runs_visible_to(self.request.user)

    def get_serializer_class(self) -> type[WorkflowRunSummarySerializer]:
        """List only; triggering returns the full run."""
        return WorkflowRunSummarySerializer

    @extend_schema(
        request=TriggerSerializer,
        responses={
            202: WorkflowRunSerializer,
            400: OpenApiResponse(description="The body is not a valid trigger."),
            404: OpenApiResponse(description="No such published definition."),
            422: OpenApiResponse(description="Idempotency-Key reused with a different body."),
        },
        parameters=[
            OpenApiParameter(
                name=IDEMPOTENCY_HEADER,
                location=OpenApiParameter.HEADER,
                required=False,
                description=(
                    "Replaying the same key returns the original run instead of "
                    "creating a second one."
                ),
            )
        ],
        tags=["runs"],
    )
    def post(self, request: Request) -> Response:
        """Trigger a run of a published definition.

        The idempotency key is optional, and its absence is not an error — it
        just means the caller accepts that a retry creates a second run.
        """
        trigger = TriggerSerializer(data=request.data)
        trigger.is_valid(raise_exception=True)
        workflow = get_object_or_404(
            Workflow,
            name=trigger.validated_data["name"],
            version=trigger.validated_data["version"],
        )

        key = request.headers.get(IDEMPOTENCY_HEADER) or None
        run, created = run_service.trigger(
            workflow,
            requested_by_id=request.user.pk,
            idempotency_key=key,
            fingerprint=run_service.request_fingerprint(trigger.validated_data),
        )

        payload = WorkflowRunSerializer(_with_task_runs(run)).data
        response = Response(payload, status=status.HTTP_202_ACCEPTED)
        if not created:
            response["Idempotent-Replay"] = "true"
        return response


class RunDetailView(RetrieveAPIView[WorkflowRun]):
    """One run and the state of each of its tasks.

    A run belonging to somebody else is a 403, not a 404: the row exists and the
    caller is simply not allowed to read it. Hiding it would be a second,
    contradictory policy layered on the first.
    """

    permission_classes = [IsAuthenticatedAndActive, IsOwnerOrStaff]
    serializer_class = WorkflowRunSerializer
    throttle_scope = "read"

    def get_queryset(self) -> QuerySet[WorkflowRun]:
        """Runs with everything the serializer reads, in one query."""
        return WorkflowRun.objects.select_related("workflow").prefetch_related("task_runs__task")


class RunEventsView(ListAPIView[TaskRunEvent]):
    """The append-only event log for one run, in the order it happened.

    A run belonging to somebody else is a 404, not a 403 — the same visibility
    rule as :class:`RunDetailView`, so a caller cannot learn that another user's
    run exists by probing for its events. The list is returned as a plain array,
    oldest first: the default cursor pagination orders newest-first, which is the
    wrong direction for a chronological log, and the per-run event count is
    bounded by the number of tasks and their attempts.
    """

    permission_classes = [IsAuthenticatedAndActive]
    serializer_class = TaskRunEventSerializer
    throttle_scope = "read"
    pagination_class = None
    # A base queryset for schema introspection; `get_queryset` below is what runs.
    queryset = TaskRunEvent.objects.none()

    def get_queryset(self) -> QuerySet[TaskRunEvent]:
        """Events for the run, oldest first, with each task's key prefetched."""
        run = get_object_or_404(
            run_service.runs_visible_to(self.request.user), pk=self.kwargs["pk"]
        )
        return (
            TaskRunEvent.objects.filter(task_run__workflow_run=run)
            .select_related("task_run__task")
            .order_by("created_at", "id")
        )


class TaskRunRequeueView(APIView):
    """Re-run a ``FAILED`` task run for a fresh attempt (manual retry).

    A task run that is not ``FAILED`` is a 409, not a silent no-op: the caller
    asked for a specific recovery and it did not happen, so it must be told. A
    task run belonging to somebody else's run is a 404 — the same visibility rule
    as everywhere else, enforced by scoping the lookup to the caller's runs.
    """

    permission_classes = [IsAuthenticatedAndActive]
    throttle_scope = "trigger"

    @extend_schema(
        request=None,
        responses={
            200: TaskRunSerializer,
            409: OpenApiResponse(description="The task run is not FAILED."),
            401: OpenApiResponse(description="Missing or invalid credentials."),
            404: OpenApiResponse(description="No such task run, or not visible to the caller."),
        },
        tags=["task runs"],
    )
    def post(self, request: Request, pk: Any) -> Response:
        """Requeue the task run and return its refreshed state."""
        task_run = get_object_or_404(
            TaskRun.objects.filter(workflow_run__in=run_service.runs_visible_to(request.user)),
            pk=pk,
        )
        if not requeue_failed_task_run(task_run_id=task_run.id):
            return Response(
                {"detail": "Task run is not FAILED; only a failed task can be re-run."},
                status=status.HTTP_409_CONFLICT,
            )
        task_run.refresh_from_db()
        return Response(TaskRunSerializer(task_run).data, status=status.HTTP_200_OK)


def _with_tasks(workflow: Workflow) -> Workflow:
    """Return ``workflow`` with its tasks loaded, for the full serializer."""
    return Workflow.objects.prefetch_related("tasks").get(pk=workflow.pk)


def _with_task_runs(run: WorkflowRun) -> WorkflowRun:
    """Return ``run`` with task runs and their tasks loaded."""
    return (
        WorkflowRun.objects.select_related("workflow")
        .prefetch_related("task_runs__task")
        .get(pk=run.pk)
    )
