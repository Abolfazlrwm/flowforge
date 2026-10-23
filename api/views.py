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
from core.models import ApiKey, Membership, TaskRun, TaskRunEvent, Workflow, WorkflowRun
from core.observability import context as obs_context
from core.observability import metrics as obs_metrics
from core.observability import tracing
from core.repositories.definitions import publish_definition
from core.repositories.transitions import requeue_failed_task_run
from core.services import api_keys as api_key_service
from core.services import audit as audit_service
from core.services import memberships as membership_service
from core.services import runs as run_service
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Count, QuerySet
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import SAFE_METHODS, AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from api import schema as _schema_extensions  # noqa: F401 - registers the OpenAPI auth scheme
from api import tenancy
from api.filters import WorkflowFilter, WorkflowRunFilter
from api.pagination import StandardCursorPagination
from api.permissions import (
    HasApiKeyScope,
    IsAdmin,
    IsAuthenticatedAndActive,
    IsMember,
    IsViewer,
)
from api.serializers import (
    ApiKeyCreatedSerializer,
    ApiKeyCreateSerializer,
    ApiKeySerializer,
    MembershipCreateSerializer,
    MembershipRoleSerializer,
    MembershipSerializer,
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


class MetricsView(APIView):
    """Prometheus metrics for the web process.

    The body is aggregate only — state counts, outbox depth and lag, DEAD count,
    due triggers — with no tenant identifier, username or task key. It is
    deliberately unauthenticated so a Prometheus sidecar can scrape it without a
    credential; deploy it on a private network (the compose ``observability``
    profile does). It is read-only and cheap: every gauge is an indexed count
    computed at scrape time.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "read"

    @extend_schema(
        responses={
            (200, "text/plain"): OpenApiResponse(description="Prometheus text exposition format.")
        },
        tags=["observability"],
    )
    def get(self, _request: Request) -> HttpResponse:
        """Render the registry in the Prometheus text format."""
        return HttpResponse(
            obs_metrics.render_metrics(),
            content_type="text/plain; version=0.0.4; charset=utf-8",
        )


class WorkflowListCreateView(ListAPIView[Workflow]):
    """List published definitions, or publish a new one."""

    permission_classes = [IsViewer]
    pagination_class = StandardCursorPagination
    filterset_class = WorkflowFilter
    ordering = ["-created_at"]

    def get_permissions(self) -> list[Any]:
        """Reading needs membership; publishing needs the admin role."""
        if self.request.method in SAFE_METHODS:
            self.required_scope = "read"
            return [IsViewer(), HasApiKeyScope()]
        self.required_scope = "admin"
        return [IsAdmin(), HasApiKeyScope()]

    def get_throttles(self) -> list[Any]:
        """Reads and publishes are throttled at different rates."""
        self.throttle_scope = "read" if self.request.method == "GET" else "trigger"
        return super().get_throttles()

    def get_queryset(self) -> QuerySet[Workflow]:
        """This organization's definitions with their task count, newest first."""
        return (
            _workflow_queryset(self.request)
            .annotate(task_count=Count("tasks"))
            .order_by("-created_at", "-id")
        )

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
        workflow, created = publish_definition(
            definition, organization=tenancy.require_active_organization(request)
        )
        if created:
            audit_service.record(
                organization=workflow.organization,
                action="workflow.published",
                target_type="workflow",
                target_id=workflow.pk,
                actor=request.user,
                metadata={"name": workflow.name, "version": workflow.version},
            )
        payload = WorkflowSerializer(_with_tasks(workflow)).data
        return Response(payload, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class WorkflowDetailView(RetrieveAPIView[Workflow]):
    """One published definition, by name and version.

    `(name, version)` is the domain's identity for a definition, so the URL uses
    it rather than the row's UUID. A client that knows which definition it wants
    already knows both values; making it look up an id first would be a second
    round trip for nothing.
    """

    permission_classes = [IsViewer, HasApiKeyScope]
    required_scope = "read"
    serializer_class = WorkflowSerializer
    throttle_scope = "read"
    lookup_field = "name"

    def get_queryset(self) -> QuerySet[Workflow]:
        """This organization's definitions, tasks prefetched to avoid N+1."""
        return _workflow_queryset(self.request).prefetch_related("tasks")

    def get_object(self) -> Workflow:
        """Resolve `(name, version)` from the URL."""
        return get_object_or_404(
            self.get_queryset(),
            name=self.kwargs["name"],
            version=self.kwargs["version"],
        )


class RunListCreateView(ListAPIView[WorkflowRun]):
    """List the caller's runs, or trigger a new one."""

    permission_classes = [IsViewer]
    pagination_class = StandardCursorPagination
    filterset_class = WorkflowRunFilter
    ordering = ["-created_at"]

    def get_permissions(self) -> list[Any]:
        """Reading needs membership; triggering needs the member role."""
        if self.request.method in SAFE_METHODS:
            self.required_scope = "read"
            return [IsViewer(), HasApiKeyScope()]
        self.required_scope = "write"
        return [IsMember(), HasApiKeyScope()]

    def get_throttles(self) -> list[Any]:
        """Triggering is expensive; listing is not."""
        self.throttle_scope = "trigger" if self.request.method == "POST" else "read"
        return super().get_throttles()

    def get_queryset(self) -> QuerySet[WorkflowRun]:
        """This organization's runs, unless the caller is staff."""
        return _run_queryset(self.request)

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
        # Scoped to the caller's organization: another tenant's definition of the
        # same name and version is not visible, so it is a 404 rather than a run.
        workflow = get_object_or_404(
            _workflow_queryset(request),
            name=trigger.validated_data["name"],
            version=trigger.validated_data["version"],
        )

        key = request.headers.get(IDEMPOTENCY_HEADER) or None
        correlation_id = obs_context.current_correlation_id()
        obs_context.bind_request_context(
            correlation_id=correlation_id,
            organization_id=workflow.organization_id,
        )
        tracing.configure_tracing("web")
        # The span is the root the scheduler and the worker continue. Its
        # traceparent is stored on the run because the outbox crosses a process
        # boundary where no live span exists.
        with tracing.start_span(
            "run.trigger",
            attributes={
                "flowforge.organization_id": str(workflow.organization_id or ""),
                "flowforge.workflow": workflow.name,
                "flowforge.workflow_version": workflow.version,
            },
        ):
            run, created = run_service.trigger(
                workflow,
                requested_by_id=request.user.pk,
                organization=workflow.organization,
                idempotency_key=key,
                fingerprint=run_service.request_fingerprint(trigger.validated_data),
                correlation_id=correlation_id,
                traceparent=tracing.current_traceparent(),
            )

        if created:
            audit_service.record(
                organization=run.organization,
                action="run.triggered",
                target_type="run",
                target_id=run.pk,
                actor=request.user,
                metadata={"workflow": workflow.name, "version": workflow.version},
            )
        payload = WorkflowRunSerializer(_with_task_runs(run)).data
        response = Response(payload, status=status.HTTP_202_ACCEPTED)
        if not created:
            response["Idempotent-Replay"] = "true"
        return response


class RunDetailView(RetrieveAPIView[WorkflowRun]):
    """One run and the state of each of its tasks.

    A run belonging to another tenant is a 404, not a 403: the queryset is
    scoped to the caller's organization, so the row is invisible by construction.
    That is the whole point of resolving visibility in the queryset rather than
    in an object-level permission — a caller cannot learn that another tenant's
    run exists by probing for its id.
    """

    permission_classes = [IsViewer, HasApiKeyScope]
    required_scope = "read"
    serializer_class = WorkflowRunSerializer
    throttle_scope = "read"

    def get_queryset(self) -> QuerySet[WorkflowRun]:
        """This organization's runs, with everything the serializer reads."""
        return _run_queryset(self.request).prefetch_related("task_runs__task")


class RunEventsView(ListAPIView[TaskRunEvent]):
    """The append-only event log for one run, in the order it happened.

    A run belonging to somebody else is a 404, not a 403 — the same visibility
    rule as :class:`RunDetailView`, so a caller cannot learn that another user's
    run exists by probing for its events. The list is returned as a plain array,
    oldest first: the default cursor pagination orders newest-first, which is the
    wrong direction for a chronological log, and the per-run event count is
    bounded by the number of tasks and their attempts.
    """

    permission_classes = [IsViewer, HasApiKeyScope]
    required_scope = "read"
    serializer_class = TaskRunEventSerializer
    throttle_scope = "read"
    pagination_class = None
    # A base queryset for schema introspection; `get_queryset` below is what runs.
    queryset = TaskRunEvent.objects.none()

    def get_queryset(self) -> QuerySet[TaskRunEvent]:
        """Events for the run, oldest first, with each task's key prefetched."""
        run = get_object_or_404(_run_queryset(self.request), pk=self.kwargs["pk"])
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

    permission_classes = [IsMember, HasApiKeyScope]
    required_scope = "write"
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
        # Scoped to the caller's organization, so another tenant's task run is a
        # 404 rather than a 403 or a cross-tenant write.
        task_run = get_object_or_404(
            TaskRun.objects.filter(workflow_run__in=_run_queryset(request)),
            pk=pk,
        )
        if not requeue_failed_task_run(task_run_id=task_run.id):
            return Response(
                {"detail": "Task run is not FAILED; only a failed task can be re-run."},
                status=status.HTTP_409_CONFLICT,
            )
        audit_service.record(
            organization=task_run.organization,
            action="task_run.requeued",
            target_type="task_run",
            target_id=task_run.pk,
            actor=request.user,
        )
        task_run.refresh_from_db()
        return Response(TaskRunSerializer(task_run).data, status=status.HTTP_200_OK)


class ApiKeyListCreateView(APIView):
    """List the organization's API keys, or issue a new one.

    Listing never exposes a secret: the stored value is a hash and the response
    carries only metadata. Creation returns the plaintext key **exactly once**,
    which is the only moment it can be captured.
    """

    permission_classes = [IsAdmin, HasApiKeyScope]
    required_scope = "admin"
    throttle_scope = "read"

    def get_throttles(self) -> list[Any]:
        """Issuing a key is a write; listing is a read."""
        self.throttle_scope = "read" if self.request.method in SAFE_METHODS else "trigger"
        return super().get_throttles()

    @extend_schema(responses={200: ApiKeySerializer(many=True)}, tags=["api keys"])
    def get(self, request: Request) -> Response:
        """List the active organization's keys (metadata only)."""
        keys = _api_key_queryset(request).order_by("-created_at")
        return Response(ApiKeySerializer(keys, many=True).data)

    @extend_schema(
        request=ApiKeyCreateSerializer,
        responses={201: ApiKeyCreatedSerializer},
        tags=["api keys"],
    )
    def post(self, request: Request) -> Response:
        """Issue a key and return its plaintext value once."""
        body = ApiKeyCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        organization = tenancy.require_active_organization(request)
        api_key, plaintext = api_key_service.create_api_key(
            organization=organization,
            name=body.validated_data["name"],
            scopes=list(body.validated_data.get("scopes") or ["read"]),
            created_by=request.user,
            expires_at=body.validated_data.get("expires_at"),
        )
        audit_service.record(
            organization=organization,
            action="api_key.created",
            target_type="api_key",
            target_id=api_key.pk,
            actor=request.user,
            metadata={"name": api_key.name, "scopes": api_key.scopes},
        )
        payload = ApiKeyCreatedSerializer(api_key, context={"plaintext": plaintext}).data
        return Response(payload, status=status.HTTP_201_CREATED)


class ApiKeyRotateView(APIView):
    """Revoke a key and issue a replacement, returning the new plaintext once."""

    permission_classes = [IsAdmin, HasApiKeyScope]
    required_scope = "admin"
    throttle_scope = "trigger"

    @extend_schema(
        request=None,
        responses={200: ApiKeyCreatedSerializer},
        tags=["api keys"],
    )
    def post(self, request: Request, pk: Any) -> Response:
        """Rotate the key named by ``pk`` within the caller's organization."""
        api_key = get_object_or_404(_api_key_queryset(request), pk=pk)
        new_key, plaintext = api_key_service.rotate_api_key(api_key=api_key)
        audit_service.record(
            organization=new_key.organization,
            action="api_key.rotated",
            target_type="api_key",
            target_id=new_key.pk,
            actor=request.user,
            metadata={"name": new_key.name, "rotated_from": str(api_key.pk)},
        )
        payload = ApiKeyCreatedSerializer(new_key, context={"plaintext": plaintext}).data
        return Response(payload, status=status.HTTP_200_OK)


class ApiKeyRevokeView(APIView):
    """Revoke a key. The row is kept so the credential's history stays auditable."""

    permission_classes = [IsAdmin, HasApiKeyScope]
    required_scope = "admin"
    throttle_scope = "trigger"

    @extend_schema(
        request=None,
        responses={200: ApiKeySerializer},
        tags=["api keys"],
    )
    def post(self, request: Request, pk: Any) -> Response:
        """Revoke the key named by ``pk`` within the caller's organization."""
        api_key = get_object_or_404(_api_key_queryset(request), pk=pk)
        api_key_service.revoke_api_key(api_key=api_key)
        audit_service.record(
            organization=api_key.organization,
            action="api_key.revoked",
            target_type="api_key",
            target_id=api_key.pk,
            actor=request.user,
            metadata={"name": api_key.name},
        )
        api_key.refresh_from_db()
        return Response(ApiKeySerializer(api_key).data, status=status.HTTP_200_OK)


class MemberListCreateView(APIView):
    """List the organization's members, or add one.

    Listing exposes usernames and e-mail addresses, so it needs the admin role.
    Adding a member is a role change too, and the service enforces that only an
    owner may grant the owner role.
    """

    permission_classes = [IsAdmin, HasApiKeyScope]
    required_scope = "admin"
    throttle_scope = "read"

    def get_throttles(self) -> list[Any]:
        """Listing is a read; adding a member is a write."""
        self.throttle_scope = "read" if self.request.method in SAFE_METHODS else "trigger"
        return super().get_throttles()

    @extend_schema(responses={200: MembershipSerializer(many=True)}, tags=["members"])
    def get(self, request: Request) -> Response:
        """List the active organization's members."""
        members = _membership_queryset(request).select_related("user").order_by("user__username")
        return Response(MembershipSerializer(members, many=True).data)

    @extend_schema(
        request=MembershipCreateSerializer,
        responses={201: MembershipSerializer},
        tags=["members"],
    )
    def post(self, request: Request) -> Response:
        """Add an existing user to the organization with a role."""
        body = MembershipCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        organization = tenancy.require_active_organization(request)
        target = get_object_or_404(User, username=body.validated_data["username"], is_active=True)
        actor_role = _actor_role(request)
        # The change and its audit entry commit together: an audit trail that can
        # disagree with the membership table is not an audit trail.
        with transaction.atomic():
            membership = membership_service.add_member(
                organization=organization,
                target_user=target,
                role=body.validated_data["role"],
                actor_role=actor_role,
            )
            audit_service.record(
                organization=organization,
                action="member.added",
                target_type="membership",
                target_id=membership.pk,
                actor=request.user,
                metadata={"username": target.username, "role": membership.role},
            )
        return Response(MembershipSerializer(membership).data, status=status.HTTP_201_CREATED)


class MemberDetailView(APIView):
    """Change a member's role, or remove the member.

    The rules (only an owner may touch an owner; the last owner is permanent) are
    enforced in ``core.services.memberships``, not here, so the same rules apply
    to any future caller.
    """

    permission_classes = [IsAdmin, HasApiKeyScope]
    required_scope = "admin"
    throttle_scope = "trigger"

    @extend_schema(
        request=MembershipRoleSerializer,
        responses={200: MembershipSerializer},
        tags=["members"],
    )
    def patch(self, request: Request, pk: Any) -> Response:
        """Change the member's role."""
        membership = get_object_or_404(_membership_queryset(request).select_related("user"), pk=pk)
        body = MembershipRoleSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        actor_role = _actor_role(request)
        previous = membership.role
        with transaction.atomic():
            membership = membership_service.change_role(
                membership=membership,
                role=body.validated_data["role"],
                actor_role=actor_role,
            )
            if membership.role != previous:
                audit_service.record(
                    organization=membership.organization,
                    action="member.role_changed",
                    target_type="membership",
                    target_id=membership.pk,
                    actor=request.user,
                    metadata={
                        "username": membership.user.username,
                        "from": previous,
                        "to": membership.role,
                    },
                )
        return Response(MembershipSerializer(membership).data)

    @extend_schema(
        request=None,
        responses={204: OpenApiResponse(description="The member was removed.")},
        tags=["members"],
    )
    def delete(self, request: Request, pk: Any) -> Response:
        """Remove the member from the organization."""
        membership = get_object_or_404(_membership_queryset(request).select_related("user"), pk=pk)
        actor_role = _actor_role(request)
        # Snapshot before delete(): Django clears the instance's pk afterwards.
        membership_id = membership.pk
        organization = membership.organization
        username = membership.user.username
        role = membership.role
        with transaction.atomic():
            membership_service.remove_member(membership=membership, actor_role=actor_role)
            audit_service.record(
                organization=organization,
                action="member.removed",
                target_type="membership",
                target_id=membership_id,
                actor=request.user,
                metadata={"username": username, "role": role},
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


def _actor_role(request: Request) -> str:
    """The role the caller is acting with in the active organization.

    Staff bypass the role check, so they act as an owner. An API key acts as its
    creator, whose membership ``resolve_membership`` returns.
    """
    if _is_staff_bypass(request):
        return Membership.Role.OWNER.value
    membership = tenancy.resolve_membership(request)
    return membership.role if membership is not None else Membership.Role.VIEWER.value


def _membership_queryset(request: Request) -> QuerySet[Membership]:
    """The memberships this caller may manage, scoped to their organization."""
    if _is_staff_bypass(request):
        return Membership.objects.all()
    organization = tenancy.get_active_organization(request)
    if organization is None:
        return Membership.objects.none()
    return Membership.objects.filter(organization=organization)


def _is_staff_bypass(request: Request) -> bool:
    """Whether this request gets the staff cross-tenant read bypass.

    A request authenticated by an API key never does: a key is bound to exactly
    one organization, so even a key created by a staff user is scoped to that
    organization. Letting a key inherit the staff bypass would turn one
    organization's credential into a read-all credential.
    """
    return bool(request.user.is_staff and getattr(request, "api_key", None) is None)


def _api_key_queryset(request: Request) -> QuerySet[ApiKey]:
    """The API keys this caller may manage, scoped to their organization."""
    if _is_staff_bypass(request):
        return ApiKey.objects.all()
    organization = tenancy.get_active_organization(request)
    if organization is None:
        return ApiKey.objects.none()
    return ApiKey.objects.for_organization(organization)


def _workflow_queryset(request: Request) -> QuerySet[Workflow]:
    """The definitions this caller may see.

    Tenant scoping is done here, once, so every workflow view — list, detail and
    the trigger lookup — inherits it. Staff are the only exception, and it is
    deliberate: they are platform operators, not members of a tenant. An
    ordinary caller with no resolvable organization gets an empty queryset rather
    than a cross-tenant one.
    """
    if _is_staff_bypass(request):
        return Workflow.objects.all()
    organization = tenancy.get_active_organization(request)
    if organization is None:
        return Workflow.objects.none()
    return Workflow.objects.for_organization(organization)


def _run_queryset(request: Request) -> QuerySet[WorkflowRun]:
    """The runs this caller may see, scoped to their organization."""
    if _is_staff_bypass(request):
        return WorkflowRun.objects.select_related("workflow").order_by("-created_at", "-id")
    organization = tenancy.get_active_organization(request)
    if organization is None:
        return WorkflowRun.objects.none()
    return run_service.runs_visible_to(organization)


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
