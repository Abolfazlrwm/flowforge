"""Cursor pagination, and why not page numbers.

Offset pagination is wrong for these tables. A client paging through workflow
runs by offset will skip or repeat rows whenever a run is created between two
requests, and with an append-heavy table that is the normal case rather than an
edge case. A cursor is a position in a stable ordering, so a concurrent insert
cannot shift the page boundary.

The ordering **must** be unique or the cursor can land in the middle of a group
of equal values. `created_at` alone is not unique — two runs created in the same
millisecond would tie — so the primary key is appended as a tiebreaker.
"""

from __future__ import annotations

from rest_framework.pagination import CursorPagination


class StandardCursorPagination(CursorPagination):
    """Newest first, with a stable tiebreaker.

    ``page_size`` is capped rather than trusted: without ``max_page_size`` a
    client can ask for a million rows and turn a list endpoint into a way to
    exhaust the database connection's memory.
    """

    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
    # `-created_at` alone is not unique; the UUID tiebreaker is what makes the
    # cursor stable when two rows share a timestamp.
    ordering = ("-created_at", "-id")
