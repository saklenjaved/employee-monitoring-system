"""Removing and transferring people. These changes touch several apps at once.

Each step uses the owning app's service: accounts for memberships, devices for devices, and
this app for teams and employee records. One change runs in one transaction with its audit
entries. Monitoring events, screenshots, daily summaries and the old employee record are
never deleted: they stay in the old organization as history.
"""
from django.db import transaction

from accounts.models import EndReason
from accounts.services import assign_role, awaiting_role_membership, end_membership, open_membership_in
from accounts.transfers import DONE_MESSAGE, check_transfer_code, finish_transfer
from audit.services import record_event
from devices.services import revoke_devices_for

from .models import EmployeeProfile, UserTeam


def _close_access(actor, organization, user):
    """Stop everything that lets `user` work in `organization`. Returns (devices revoked, team links removed).

    The person's screenshot exclusion in that organization is kept on purpose: it only limits
    capture, and it still applies if the person is invited back.
    """
    revoked = revoke_devices_for(actor, organization, user)
    team_links, _ = UserTeam.objects.filter(organization=organization, user=user).delete()
    return revoked, team_links


def remove_person(actor, person):
    """Admin: remove an Employee or Manager from the organization.

    The membership ends as "removed", the account is turned off and the person's devices in the
    organization stop working. The account and all history stay. The person can be invited again.
    """
    if person.pk == actor.pk:
        raise ValueError("You cannot remove yourself.")
    membership = open_membership_in(person, actor.organization)
    if membership is None:
        raise ValueError("This person is not a member of your organization.")
    with transaction.atomic():
        end_membership(membership, EndReason.REMOVED)
        revoked, team_links = _close_access(actor, membership.organization, person)
        record_event(
            actor,
            "membership.removed",
            organization=membership.organization,
            target=person,
            detail=f"{revoked} device(s) revoked, {team_links} team link(s) removed",
        )


def complete_transfer(request, email, code):
    """The person confirms a transfer with their code. Returns (transfer, error_message).

    On success the old membership ends, the old organization's devices and team links are
    closed, and a pending membership waits in the destination for its role.
    """
    transfer, error = check_transfer_code(request, email, code)
    if error:
        return None, error
    source = transfer.source_membership
    with transaction.atomic():
        if finish_transfer(transfer) is None:  # another request used the code first
            return None, DONE_MESSAGE
        actor = transfer.requested_by or transfer.user
        revoked, team_links = _close_access(actor, source.organization, transfer.user)
        record_event(
            actor,
            "membership.access_closed",
            organization=source.organization,
            target=transfer.user,
            detail=f"transfer {transfer.pk}: {revoked} device(s) revoked, {team_links} team link(s) removed",
        )
    return transfer, ""


def assign_transferred_role(actor, person, role):
    """Destination Admin: give a transferred person their role and an empty employee record."""
    membership = awaiting_role_membership(person)
    if membership is None:
        raise ValueError("This person is not waiting for a role.")
    with transaction.atomic():
        assign_role(actor, membership, role)
        EmployeeProfile.objects.get_or_create(user=person, organization=membership.organization)
    return membership
