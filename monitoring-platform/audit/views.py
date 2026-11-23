"""The audit log page. Admins see their own organization; the Super Admin sees every organization.

Audit entries can never be changed. Admins can only read them. Only the Super Admin can delete entries,
one at a time or in bulk, and every deletion leaves a permanent AuditDeletion record.

Times are stored in UTC. They are converted to settings.DISPLAY_TIMEZONE once, here, when the page is built.
"""
from datetime import date, datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from types import SimpleNamespace

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.timesince import timesince
from django.views.decorators.http import require_POST

from access_control.permissions import role_required
from accounts.models import Role
from common.listing import Column, Filter, build_listing
from organizations.models import Organization

from .models import AuditDeletion, AuditLog
from .services import delete_entries

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


def _build_listing(request, viewer):
    """The filtered, searched and sorted entries for `request.GET`. Returns (listing, visible, date_error).

    The list page and the delete action both call this, so "every entry matching the filters" always means
    exactly what the page shows. `visible` is the viewer's whole log, before any filter.
    """
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
    return listing, visible, date_error


@role_required(Role.ADMIN, Role.SUPER_ADMIN)
def audit_log_list(request):
    viewer = request.user
    listing, visible, date_error = _build_listing(request, viewer)
    page = listing["page"]
    page.object_list = [_show(entry) for entry in page.object_list]
    # The pager keeps the date range too, so the next page shows the same dates.
    dates = {key: value for key, value in (("from", request.GET.get("from", "")), ("to", request.GET.get("to", ""))) if value and not date_error}
    if dates:
        listing["querystring"] = f"{listing['querystring']}&{urlencode(dates)}" if listing["querystring"] else urlencode(dates)
    listing["filtered"] = listing["filtered"] or bool(dates)

    context = {
        "listing": listing,
        "summary": _summary(visible),
        "date_from": request.GET.get("from", ""),
        "date_to": request.GET.get("to", ""),
        "date_error": date_error,
        "zone_label": datetime.now(DISPLAY_ZONE).strftime("%Z"),
        "can_delete": viewer.role == Role.SUPER_ADMIN,
    }
    if context["can_delete"]:
        context["deletions"] = AuditDeletion.objects.all()[:10]
    return render(request, "audit/log_list.html", context)


KEPT_QUERY_KEYS = ("q", "action", "status", "organization", "from", "to", "sort", "dir")


@role_required(Role.SUPER_ADMIN)
@require_POST
def audit_log_delete(request):
    """Delete audit entries. Only the Super Admin gets here (the decorator, not the hidden buttons, enforces it).

    One entry ("single"), the ticked entries ("selected"), or every entry matching the filters ("matching").
    """
    keep = {key: request.POST[key] for key in KEPT_QUERY_KEYS if request.POST.get(key)}
    back = reverse("audit_log_list") + (f"?{urlencode(keep)}" if keep else "")
    single = request.POST.get("single", "")
    if single:
        method, ids = "single", [single]
    elif request.POST.get("select_matching") == "1":
        # The filters travel with the form. A bad date range is refused, so it never deletes more than the page showed.
        listing, _, date_error = _build_listing(SimpleNamespace(GET=request.POST), request.user)
        if date_error:
            messages.error(request, date_error)
            return redirect(back)
        method, ids = "matching", None
    else:
        method, ids = "selected", request.POST.getlist("entry")
    if method == "matching":
        entries = listing["rows"]
    else:
        valid_ids = [int(value) for value in ids if str(value).isdigit()]
        if not valid_ids:
            messages.error(request, "Select at least one audit entry to delete.")
            return redirect(back)
        entries = AuditLog.objects.filter(pk__in=valid_ids)
    deleted = delete_entries(request.user, entries, method)
    if deleted:
        noun = "entry" if deleted == 1 else "entries"
        messages.success(request, f"Deleted {deleted} audit {noun}. The deletion was recorded.")
    else:
        messages.info(request, "Nothing was deleted.")
    return redirect(back)
