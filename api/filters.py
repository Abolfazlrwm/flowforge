"""Query filters for the list endpoints.

Filtering lives here rather than in the view so that the allowed parameters are a
declared, testable set rather than whatever a view happens to read out of
`request.query_params`. An unknown parameter is ignored by Django's filter
backend, which is why the set has to be explicit: silently ignoring
`?state=runing` returns everything and looks like a filter that does not work.
"""

from __future__ import annotations

from typing import ClassVar

import django_filters
from core.models import Workflow, WorkflowRun


class WorkflowFilter(django_filters.FilterSet):
    """Filter published definitions by name, and by a name prefix."""

    name = django_filters.CharFilter(lookup_expr="exact")
    name__startswith = django_filters.CharFilter(field_name="name", lookup_expr="startswith")

    class Meta:
        """Model and allowed fields."""

        model = Workflow
        fields: ClassVar[list[str]] = ["name", "version"]


class WorkflowRunFilter(django_filters.FilterSet):
    """Filter runs by state, by definition, and by creation date."""

    state = django_filters.CharFilter(lookup_expr="exact")
    workflow = django_filters.CharFilter(field_name="workflow__name", lookup_expr="exact")
    created_after = django_filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="gte")
    created_before = django_filters.IsoDateTimeFilter(field_name="created_at", lookup_expr="lte")

    class Meta:
        """Model and allowed fields."""

        model = WorkflowRun
        fields: ClassVar[list[str]] = ["state", "workflow", "created_after", "created_before"]
