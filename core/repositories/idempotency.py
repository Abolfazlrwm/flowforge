"""Idempotency claims, and the constraint that makes them work.

The rule being implemented: **N parallel requests carrying the same
``Idempotency-Key`` produce exactly one run.** That guarantee comes from the
unique index on ``(user, key)``, not from a lock, and the distinction matters:

* A row that does not exist yet cannot be locked. ``select_for_update()``
  returns nothing for it, and PostgreSQL has no gap locks to make it block. Two
  callers would both see "not claimed" and both proceed — exactly the duplicate
  this table exists to prevent.
* A unique index is enforced by the database for every writer, including one
  that never ran this code.

So the reservation is an ``INSERT`` that either wins or raises. A loser blocks
until the winner's transaction commits, which means by the time it observes the
``IntegrityError`` the winner's response has been written — there is no window
where a claim exists without an answer attached.
"""

from __future__ import annotations

from typing import Any

from core.models import IdempotencyKey, Organization


class IdempotencyMismatchError(Exception):
    """The key was reused with a different request.

    Not a conflict in the resource sense — it is a client bug. Returning the
    original response would silently discard the second request, so this is
    raised instead and mapped to 422.
    """

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(
            f"Idempotency-Key {key!r} was already used with a different request body. "
            "Use a new key for a different request."
        )


def reserve(
    *, organization: Organization | None, user_id: int, key: str, fingerprint: str
) -> IdempotencyKey:
    """Claim ``key`` for ``user`` within ``organization``.

    The claim is scoped per organization (see the model's unique constraint), so
    a key reused by a different tenant is an independent claim and can never
    replay another tenant's run.

    Must be called inside the transaction that will create the run, so that a
    failure to create the run releases the claim instead of leaving a permanent
    hole that replays as an empty response.

    Raises:
        IntegrityError: if the key is already claimed. The caller decides
            whether that is a replay or a mismatch.
    """
    return IdempotencyKey.objects.create(
        organization=organization,
        user_id=user_id,
        key=key,
        request_fingerprint=fingerprint,
        # Placeholders, overwritten by `complete` before the transaction commits.
        response_status=0,
        response_body={},
    )


def complete(
    claim: IdempotencyKey,
    *,
    status: int,
    body: dict[str, Any],
    workflow_run_id: Any | None,
) -> None:
    """Attach the response to a claim so a replay can return it verbatim."""
    claim.response_status = status
    claim.response_body = body
    claim.workflow_run_id = workflow_run_id
    claim.save(update_fields=["response_status", "response_body", "workflow_run"])


def find(*, organization: Organization | None, user_id: int, key: str) -> IdempotencyKey | None:
    """The claim for ``key`` in ``organization``, or ``None``.

    Only meaningful *after* a reservation has failed: at that point the winner
    has committed, so the row is visible and complete.
    """
    return IdempotencyKey.objects.filter(
        organization=organization, user_id=user_id, key=key
    ).first()
