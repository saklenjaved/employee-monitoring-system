from django.db import transaction

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
