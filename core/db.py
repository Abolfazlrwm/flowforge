"""Database connection hygiene for long-running processes.

A request-scoped process (the web app) gets a fresh connection per request, so a
database restart is invisible: the next request opens a new socket. A **loop**
does not. It holds one connection for its whole life, and when PostgreSQL restarts
that socket is dead — every subsequent query raises
``OperationalError: terminating connection due to administrator command``.

Django's ``close_old_connections()`` is the request-lifecycle hook and is not
enough here: it asks the connection whether it is usable, and a socket the *server*
closed can still look usable from the client side. The only reliable recovery is
to throw the connection away and let the next query open a new one.
"""

from __future__ import annotations

from django.db import connections


def reset_connections() -> None:
    """Close every Django connection so the next query opens a fresh one.

    Safe to call when nothing is open: ``close()`` on a closed connection is a
    no-op. Callers use it in the exception path of a retry loop, after a
    ``DatabaseError`` — not on the happy path, where it would add a round trip per
    iteration.
    """
    for connection in connections.all():
        connection.close()
