"""Adding, changing and removing organization memberships.

The rules live here, not in the view, because they are the same rules whether a
change arrives over HTTP, from a future CLI, or from a test. They are:

1. **Only an owner may touch an owner.** An admin can manage viewers, members and
   admins, but cannot change or remove an owner, and cannot grant the owner role.
   Without this, an admin could demote the owner who appointed them.
2. **The last owner is permanent.** Removing or demoting the only owner would
   leave an organization nobody can administer, so it is refused with a conflict.
   The owner rows are locked while the count is taken, so two concurrent
   demotions cannot each see "two owners" and both succeed.
3. **A no-op is a no-op.** Changing a role to the value it already has returns
   the membership unchanged; the caller records no audit entry for it.
"""

from __future__ import annotations

from typing import Any

from django.db import IntegrityError, transaction

from core.models import Membership, Organization

OWNER = Membership.Role.OWNER.value
ADMIN = Membership.Role.ADMIN.value


class MembershipError(Exception):
    """Base class for a membership change that cannot be applied."""


class MembershipConflictError(MembershipError):
    """The change would leave the organization in an invalid state (409)."""


class MembershipForbiddenError(MembershipError):
    """The actor's role does not permit the change (403)."""


def _require_owner_to_grant_owner(actor_role: str, role: str) -> None:
    """Only an owner may create or promote to ``owner``."""
    if role == OWNER and actor_role != OWNER:
        raise MembershipForbiddenError("Only an owner can grant the owner role.")


def _require_owner_to_modify_owner(actor_role: str, target_role: str) -> None:
    """An admin must not change or remove an owner."""
    if target_role == OWNER and actor_role != OWNER:
        raise MembershipForbiddenError("Only an owner can modify another owner.")


def _owner_ids(organization: Organization) -> list[Any]:
    """Lock and return the organization's owner membership ids.

    Called inside ``transaction.atomic()``. The lock is what makes the
    last-owner check race-safe: a second demotion waits, then re-reads a shorter
    list. SQLite ignores ``select_for_update`` but serializes writers anyway.
    """
    return list(
        Membership.objects.select_for_update()
        .filter(organization=organization, role=OWNER)
        .values_list("pk", flat=True)
    )


def add_member(
    *, organization: Organization, target_user: Any, role: str, actor_role: str
) -> Membership:
    """Add ``target_user`` to ``organization`` with ``role``.

    Raises:
        MembershipForbiddenError: an admin tried to grant ``owner``.
        MembershipConflictError: the user is already a member.
    """
    _require_owner_to_grant_owner(actor_role, role)
    if Membership.objects.filter(organization=organization, user=target_user).exists():
        raise MembershipConflictError("That user is already a member of this organization.")
    try:
        return Membership.objects.create(organization=organization, user=target_user, role=role)
    except IntegrityError as exc:  # a concurrent add won the unique constraint
        raise MembershipConflictError(
            "That user is already a member of this organization."
        ) from exc


def change_role(*, membership: Membership, role: str, actor_role: str) -> Membership:
    """Change ``membership``'s role, enforcing the owner rules.

    Raises:
        MembershipForbiddenError: the actor may not modify this membership.
        MembershipConflictError: the change would demote the last owner.
    """
    _require_owner_to_modify_owner(actor_role, membership.role)
    _require_owner_to_grant_owner(actor_role, role)
    if membership.role == role:
        return membership
    with transaction.atomic():
        if membership.role == OWNER and role != OWNER:
            owners = _owner_ids(membership.organization)
            if len(owners) <= 1:
                raise MembershipConflictError("The last owner cannot be demoted.")
        membership.role = role
        membership.save(update_fields=["role"])
    return membership


def remove_member(*, membership: Membership, actor_role: str) -> None:
    """Remove ``membership`` from its organization.

    Raises:
        MembershipForbiddenError: the actor may not modify this membership.
        MembershipConflictError: the membership is the last owner.
    """
    _require_owner_to_modify_owner(actor_role, membership.role)
    with transaction.atomic():
        if membership.role == OWNER:
            owners = _owner_ids(membership.organization)
            if len(owners) <= 1:
                raise MembershipConflictError("The last owner cannot be removed.")
        membership.delete()
