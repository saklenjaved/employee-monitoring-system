from django.db import transaction

from accounts.models import MembershipStatus, Role
from accounts.services import set_user_active
from audit.services import record_event

from .models import Organization


def create_organization(actor, name):
    with transaction.atomic():
        organization = Organization.objects.create(name=name)
        record_event(actor, "organization.created", organization=organization, target=organization)
    return organization


def set_organization_active(actor, organization, active):
    """Activate or deactivate an organization. Does nothing if nothing changes."""
    if organization.is_active == active:
        return
    with transaction.atomic():
        organization.is_active = active
        organization.save(update_fields=["is_active"])
        action = "organization.activated" if active else "organization.deactivated"
        record_event(actor, action, organization=organization, target=organization)


def set_organization_admin_active(actor, admin, active):
    """Super Admin: turn an Admin's account on or off. Nothing is deleted.

    An invitation that is still pending cannot be switched here, and a removed Admin must be invited again.
    """
    latest = admin.memberships.filter(role=Role.ADMIN).order_by("-created_at", "-pk").first()
    if latest is not None and latest.status == MembershipStatus.PENDING:
        raise ValueError("This Admin has not activated the invitation yet.")
    if active and latest is not None and latest.status == MembershipStatus.ENDED:
        raise ValueError("This Admin was removed. Invite them again instead.")
    set_user_active(actor, admin, active)
