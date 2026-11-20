"""The audit log page. Admins see their own organization; the Super Admin sees every organization.

Audit entries are read-only here. Nothing on this page can change or delete them.

Times are stored in UTC. They are converted to settings.DISPLAY_TIMEZONE once, here, when the page is built.
"""
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.conf import settings
from django.shortcuts import render
from django.utils import timezone
from django.utils.timesince import timesince

from access_control.permissions import role_required
from accounts.models import Role
from common.listing import Column, Filter, build_listing
from organizations.models import Organization

from .models import AuditLog

DISPLAY_ZONE = ZoneInfo(settings.DISPLAY_TIMEZONE)

ADMIN_COLUMNS = (
    Column("When", "when", "created_at"),
    Column("Who", "who", "actor_email", True),
    Column("Action", "action", "action", True),
    Column("Record"),
    Column("Detail"),
)
SUPER_ADMIN_COLUMNS = (
    Column("When", "when", "created_at"),
    Column("Who", "who", "actor_email", True),
    Column("Action", "action", "action", True),
    Column("Organization", "organization", "organization__name", True),
    Column("Record"),
    Column("Detail"),
)

# Actions about access, passwords, devices or sensitive screenshot access.
SECURITY_ACTIONS = (
    "user.password_changed",
    "user.password_changed_in_admin",
    "user.activated",
    "monitoring_pin.locked",
    "monitoring_pin.set",
    "device.revoked",
    "screenshot.viewed",
    "screenshot.downloaded",
    "membership.transfer_requested",
)

# The audit record has no status field. The status shown is worked out from the action name.
DEFAULT_STATUS = "Recorded"
STATUS_BY_ACTION = {
    "monitoring.start_requested": "Pending",
    "monitoring.approval_requested": "Pending",
    "monitoring.consented": "Pending",
    "monitoring.started": "Active",
    "monitoring_pin.locked": "Blocked",
    "device.revoked": "Revoked",
}
STATUS_CHOICES = [(DEFAULT_STATUS, DEFAULT_STATUS)] + [(status, status) for status in dict.fromkeys(STATUS_BY_ACTION.values())]


def entries_for(viewer):
    """The audit entries `viewer` may see: their own organization for an Admin, all for the Super Admin."""
    if viewer.role == Role.SUPER_ADMIN:
        return AuditLog.objects.all()
    if viewer.role == Role.ADMIN:
        return AuditLog.objects.filter(organization=viewer.organization)
    return AuditLog.objects.none()


def _by_status(rows, value):
    if value == DEFAULT_STATUS:
        return rows.exclude(action__in=list(STATUS_BY_ACTION))
    return rows.filter(action__in=[action for action, status in STATUS_BY_ACTION.items() if status == value])


def _date_range(request):
    """(start, end, error) for the From and To dates, in the display time zone. End is exclusive."""
    raw_from = request.GET.get("from", "").strip()
    raw_to = request.GET.get("to", "").strip()
    try:
        start = datetime.combine(date.fromisoformat(raw_from), time.min, tzinfo=DISPLAY_ZONE) if raw_from else None
        end = (
            datetime.combine(date.fromisoformat(raw_to) + timedelta(days=1), time.min, tzinfo=DISPLAY_ZONE)
            if raw_to
            else None
        )
    except ValueError:
        return None, None, "Enter the dates as YYYY-MM-DD."
    if start and end and start >= end:
        return None, None, "The From date must be on or before the To date."
    return start, end, ""


def _summary(entries):
    """Counts for the cards. Uses the whole visible log, not the current filters."""
    today = timezone.now().astimezone(DISPLAY_ZONE).date()
    today_start = datetime.combine(today, time.min, tzinfo=DISPLAY_ZONE)
    return {
        "total": entries.count(),
        "today": entries.filter(created_at__gte=today_start).count(),
        "users": entries.filter(actor__isnull=False).count(),
        "security": entries.filter(action__in=SECURITY_ACTIONS).count(),
    }


def _show(entry):
    """Adds the display values to one entry. The conversion to the display time zone happens only here."""
    local = entry.created_at.astimezone(DISPLAY_ZONE)
    entry.local_date = f"{local.day} {local:%b %Y}"  # 19 Nov 2026
    entry.local_time = local.strftime("%I:%M %p")  # 02:45 PM
    entry.ago = timesince(entry.created_at).split(",")[0]  # 5 minutes
    entry.status = STATUS_BY_ACTION.get(entry.action, DEFAULT_STATUS)
    return entry


@role_required(Role.ADMIN, Role.SUPER_ADMIN)
def audit_log_list(request):
    viewer = request.user
    visible = entries_for(viewer)
    start, end, date_error = _date_range(request)
    entries = visible.select_related("organization")
    if start:
        entries = entries.filter(created_at__gte=start)
    if end:
        entries = entries.filter(created_at__lt=end)

    actions = [(action, action) for action in visible.values_list("action", flat=True).distinct().order_by("action")]
    filters = [
        Filter("action", "Action", actions, lambda rows, value: rows.filter(action=value)),
        Filter("status", "Status", STATUS_CHOICES, _by_status),
    ]
    columns = ADMIN_COLUMNS
    if viewer.role == Role.SUPER_ADMIN:
        columns = SUPER_ADMIN_COLUMNS
        organizations = [(str(org.pk), org.name) for org in Organization.objects.order_by("name")]
        filters.append(
            Filter("organization", "Organization", organizations, lambda rows, value: rows.filter(organization_id=value))
        )
    listing = build_listing(
        request,
        entries,
        columns,
        search_fields=("action", "actor_email", "target_id", "detail"),
        filters=filters,
        default_sort="when",
        default_descending=True,
    )
    page = listing["page"]
    page.object_list = [_show(entry) for entry in page.object_list]
    # The pager keeps the date range too, so the next page shows the same dates.
    dates = {key: value for key, value in (("from", request.GET.get("from", "")), ("to", request.GET.get("to", ""))) if value and not date_error}
    if dates:
        listing["querystring"] = f"{listing['querystring']}&{urlencode(dates)}" if listing["querystring"] else urlencode(dates)
    listing["filtered"] = listing["filtered"] or bool(dates)

    return render(
        request,
        "audit/log_list.html",
        {
            "listing": listing,
            "summary": _summary(visible),
            "date_from": request.GET.get("from", ""),
            "date_to": request.GET.get("to", ""),
            "date_error": date_error,
            "zone_label": datetime.now(DISPLAY_ZONE).strftime("%Z"),
        },
    )
