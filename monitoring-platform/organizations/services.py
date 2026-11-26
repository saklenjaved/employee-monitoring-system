from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, ProtectedError, Q
from django.db.models.deletion import Collector
from django.utils import timezone

from accounts.models import MembershipStatus, Role, User
from accounts.services import platform_admins, set_user_active
from audit.services import record_event
from devices.models import Device

from .models import Organization


def organization_figures(viewer, organization):
    """Live counts for one organization, using the same rules as the platform overview.

    Admins are counted from the same list the Admin table uses, so the two numbers always agree.
    Returns counts only: no employee-level monitoring data.
    """
    cutoff = timezone.now() - timedelta(seconds=settings.DEVICE_ONLINE_SECONDS)
    admins = platform_admins(viewer).filter(organization=organization).aggregate(
        total=Count("pk"),
        active=Count("pk", filter=Q(admin_status="active")),
        pending=Count("pk", filter=Q(admin_status="pending")),
        inactive=Count("pk", filter=Q(admin_status="inactive")),
    )
    devices = Device.objects.filter(organization=organization, is_active=True)
    return {
        "admin_total": admins["total"],
        "admin_active": admins["active"],
        "admin_pending": admins["pending"],
        "admin_inactive": admins["inactive"],
        "employee_count": User.objects.filter(organization=organization, role=Role.EMPLOYEE).count(),
        "device_count": devices.count(),
        "online_count": devices.filter(last_seen_at__gte=cutoff).count(),
        "updated_label": timezone.now().astimezone(ZoneInfo(settings.DISPLAY_TIMEZONE)).strftime("%d %b %Y, %I:%M:%S %p"),
    }


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


def update_organization_admin(actor, admin, email, full_name):
    """Super Admin: change an Admin's name and email. The email must not be used by another account in the organization."""
    if actor.role != Role.SUPER_ADMIN or admin.role != Role.ADMIN or admin.organization_id is None:
        raise PermissionDenied
    if User.objects.filter(email=email, organization_id=admin.organization_id).exclude(pk=admin.pk).exists():
        raise ValueError("Another account in this organization already uses this email.")
    if User.objects.filter(email=email, role=Role.SUPER_ADMIN).exists():
        raise ValueError("This email cannot be used.")
    with transaction.atomic():
        admin.email = email
        admin.full_name = full_name
        admin.save(update_fields=["email", "full_name"])
        record_event(actor, "admin.updated", organization=admin.organization, target=admin)


def delete_organization_admin(actor, admin):
    """Super Admin: permanently delete an Admin's account and its Admin membership.

    Only the account and its Admin membership (and their invitation codes) are removed. The organization, its
    employees, devices and monitoring data are never touched. If anything else still depends on the account
    (for example a transfer), nothing is deleted and the Admin should be deactivated instead.
    """
    if actor.role != Role.SUPER_ADMIN or admin.role != Role.ADMIN or admin.organization_id is None:
        raise PermissionDenied
    organization = admin.organization
    with transaction.atomic():
        try:
            admin.memberships.filter(role=Role.ADMIN).delete()
            collector = Collector(using="default")
            collector.collect([admin])
        except ProtectedError as error:
            raise ValueError("This Admin has records that must be kept. Deactivate the account instead.") from error
        removed_labels = {model._meta.label for model in collector.data}
        if not removed_labels <= ALLOWED_ADMIN_DELETE_MODELS:
            raise ValueError("This Admin has records that must be kept. Deactivate the account instead.")
        record_event(actor, "admin.deleted", organization=organization, target=admin)
        admin.delete()


ALLOWED_ADMIN_DELETE_MODELS = {"accounts.User", "accounts.Membership", "accounts.ActivationCode", "accounts.MonitoringPin"}
