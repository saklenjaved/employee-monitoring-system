"""Dashboard pages for the four roles (PRD FR-11 to FR-14).

Each page asks analytics.services for its data. The service decides what the logged-in
user may see, so a page only turns "nothing to show" into a 404.
"""
import calendar
import csv
from urllib.parse import urlencode
from datetime import timedelta

from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from access_control.permissions import role_required
from audit.services import entries_about, record_event
from accounts.models import Role
from monitoring.models import MonitoringPolicy
from screenshots.models import ScreenshotPolicy
from monitoring.services import get_policy, report_zone, zone_context
from accounts.pin import has_pin
from devices.pairing import awaiting_first_contact, open_window_for, waiting_pcs_for
from devices.services import connection_state, sync_status
from devices.start import monitoring_status
from screenshots.services import can_view_screenshots, visible_screenshots
from workforce.services import visible_employees, visible_teams

from .forms import DateRangeForm
from .services import (
    MONITORING_GROUPS,
    activity_timeline,
    attendance_by_day,
    display_zone,
    local_day_bounds,
    employees_in_group,
    command_center,
    daily_trend,
    employee_live,
    employees_to_connect,
    live_status,
    monitoring_360_rows,
    monitoring_overview,
    organization_report,
    platform_report,
    team_report,
    teams_overview,
    trend_range,
    usage_for,
)

CATEGORIES = [
    ("productive", "Productive"),
    ("unproductive", "Unproductive"),
    ("neutral", "Neutral"),
    ("unclassified", "Unclassified"),
]

HOME_BY_ROLE = {
    Role.EMPLOYEE: "dashboard_me",
    Role.MANAGER: "dashboard_teams",
    Role.ADMIN: "dashboard_organization",
    Role.SUPER_ADMIN: "dashboard_platform",
}


def _today_for(organization):
    """Today's date in the organization's time zone."""
    return timezone.now().astimezone(report_zone()).date()


# Quick ranges: how many days before today the range starts. "day" is today only; "week" is the last 7 days.
PRESET_OFFSET_DAYS = {"day": 0, "week": 6, "month": 29}


def _date_range(request, organization):
    """The dates to show, from ?start=...&end=... Today, in the organization's time zone, by default."""
    today = _today_for(organization)
    preset = request.GET.get("preset")
    if preset in PRESET_OFFSET_DAYS:
        start = today - timedelta(days=PRESET_OFFSET_DAYS[preset])
        return start, today, DateRangeForm(initial={"start": start, "end": today})
    if "start" in request.GET or "end" in request.GET:
        form = DateRangeForm(request.GET)
        if form.is_valid() and form.cleaned_data["start"]:
            return form.cleaned_data["start"], form.cleaned_data["end"], form
    else:
        form = DateRangeForm(initial={"start": today, "end": today})
    # A missing or invalid range falls back to today. The form keeps its errors for the page.
    return today, today, form


def _page(request, template, organization, build_context):
    start, end, form = _date_range(request, organization)
    context = {"form": form, "start": start, "end": end, "categories": CATEGORIES, "preset": request.GET.get("preset")}
    context.update(build_context(start, end))
    return render(request, template, context)


@role_required()
def dashboard(request):
    """Send the user to the dashboard for their role."""
    return redirect(HOME_BY_ROLE[request.user.role])


@role_required(Role.EMPLOYEE)
def my_dashboard(request):
    user = request.user

    def context(start, end):
        connection = connection_state(user)
        sync = sync_status(user)
        waiting = list(waiting_pcs_for(user))
        setup_open = open_window_for(user) is not None
        # A PC is being connected: the installer was downloaded (setup is open) or a PC is waiting, and no PC is
        # connected yet, or the PC was just approved and its agent has not checked in yet. The card then
        # refreshes every few seconds, so the Approve button and the Connected status appear by themselves.
        approved_pc = awaiting_first_contact(user)
        connecting = (connection == "not_connected" and (bool(waiting) or setup_open)) or approved_pc is not None
        return {
            "employee": user,
            "report": usage_for(user, user, start, end),
            "own": True,
            "connection": connection,
            "sync": sync,
            "monitoring": monitoring_status(user),
            "has_pin": has_pin(user),
            "waiting_pcs": waiting,
            "setup_open": setup_open,
            "approved_pc": approved_pc,
            "connecting": connecting,
        }

    return _page(request, "analytics/employee.html", user.organization, context)


WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
RECENT_SCREENSHOTS = 12


def _monitoring_window(organization):
    """The organization's monitoring days and hours, for display. The default policy is created on first use."""
    policy = get_policy(organization)
    return {
        "enabled": policy.enabled,
        "days": ", ".join(WEEKDAY_NAMES[day] for day in sorted(policy.days)),
        "start": policy.start_time.strftime("%H:%M"),
        "end": policy.end_time.strftime("%H:%M"),
        "timezone": policy.timezone,
    }


@role_required(Role.ADMIN, Role.MANAGER)
def employee_dashboard(request, pk):
    employee = visible_employees(request.user).filter(pk=pk).first()
    if employee is None:
        raise Http404

    def context(start, end):
        can_view = can_view_screenshots(request.user)
        # Only this employee's screenshots in the chosen dates, and only those the viewer may see.
        shots, shot_total = [], 0
        if can_view:
            from_time, until_time = local_day_bounds(start, end, display_zone())
            in_range = visible_screenshots(request.user).filter(
                user=employee, captured_at__gte=from_time, captured_at__lt=until_time
            )
            shot_total = in_range.count()
            shots = list(in_range.order_by("-captured_at")[:RECENT_SCREENSHOTS])
        return {
            "employee": employee,
            "report": usage_for(request.user, employee, start, end),
            "own": False,
            "live": employee_live(employee),
            "monitoring_window": _monitoring_window(request.user.organization),
            "can_view_screenshots": can_view,
            "screenshots": shots,
            "screenshot_total": shot_total,
            "timeline": activity_timeline(employee, start, end),
            "attendance": attendance_by_day(employee, start, end),
            **zone_context(request.user.organization),
        }

    return _page(request, "analytics/employee.html", request.user.organization, context)


@role_required(Role.ADMIN, Role.MANAGER)
def employee_index(request):
    """Find an employee and open their monitoring details. Lists only the employees the viewer may see."""
    query = request.GET.get("q", "").strip()
    employees = visible_employees(request.user).order_by("full_name")
    if query:
        employees = employees.filter(Q(full_name__icontains=query) | Q(email__icontains=query))
    return render(request, "analytics/employee_index.html", {"employees": employees[:200], "query": query})


@role_required(Role.ADMIN, Role.MANAGER)
def employee_live_panel(request, pk):
    """The agent status and recent activity on an employee's page (an HTMX partial the page refreshes)."""
    employee = visible_employees(request.user).filter(pk=pk).first()
    if employee is None:
        raise Http404
    context = {
        "employee": employee,
        "live": employee_live(employee),
        "monitoring_window": _monitoring_window(request.user.organization),
        **zone_context(request.user.organization),
    }
    return render(request, "analytics/_employee_live.html", context)


@role_required(Role.MANAGER)
def teams_dashboard(request):
    return _page(
        request,
        "analytics/teams.html",
        request.user.organization,
        lambda start, end: {"rows": teams_overview(request.user, start, end)},
    )


@role_required(Role.ADMIN, Role.MANAGER)
def team_dashboard(request, pk):
    def context(start, end):
        report = team_report(request.user, pk, start, end)
        if report is None:
            raise Http404
        return {"report": report}

    return _page(request, "analytics/team.html", request.user.organization, context)


def _device_donut(agents, pending):
    """Numbers for the Device Status ring: online and offline as shares of the registered PCs, from the agents."""
    total = agents["total"] - agents["revoked"]

    def share(count):
        return round(100 * count / total) if total else 0

    online_end = share(agents["online"])
    return {
        "total": total,
        "online": agents["online"],
        "offline": agents["offline"],
        "pending": pending,
        "online_end": online_end,
        "offline_end": min(100, online_end + share(agents["offline"])),
    }


@role_required(Role.ADMIN)
def organization_dashboard(request):
    def context(start, end):
        report = organization_report(request.user, start, end)
        live = live_status(request.user)
        # Read only: a page view never creates a policy row. Without one, the defaults are shown.
        organization = request.user.organization
        policy = MonitoringPolicy.objects.filter(organization=organization).first() or MonitoringPolicy(organization=organization)
        preset = request.GET.get("preset")
        if preset in PRESET_OFFSET_DAYS:
            choice = preset
        elif "start" in request.GET or "end" in request.GET:
            choice = "custom"
        else:
            choice = "day"
        overview = monitoring_overview(request.user)
        monitored_by_name = {row["name"]: row for row in overview["teams"]}
        # One row per team for the Team Performance card: productivity from the report, monitoring from the overview.
        team_performance = []
        for row in report["teams"]:
            status = monitored_by_name.get(row["team"].name, {})
            team_performance.append({
                "pk": row["team"].pk,
                "name": row["team"].name,
                "productive_percent": row["productive_percent"],
                "monitored": status.get("monitored", 0),
                "total": status.get("total", row["employee_count"]),
            })
        return {
            "range_choice": choice,
            "report": report,
            "team_performance": team_performance,
            "live": live,
            "to_connect": employees_to_connect(request.user),
            "monitoring_rows": _filtered_rows(
                monitoring_360_rows(report["employees"], live["employees"]), request.GET.get("group", "")
            ),
            "monitoring_group": request.GET.get("group", "") if request.GET.get("group") in MONITORING_GROUPS else "",
            "monitoring_group_label": MONITORING_GROUPS.get(request.GET.get("group", ""), ""),
            "overview": overview,
            "command": command_center(request.user, start, end, activity_page=request.GET.get("page")),
            # The pager links keep the date range and other filters, and only change the activity page.
            "activity_query": urlencode({key: value for key, value in request.GET.items() if key != "page"}),
            "donut": _device_donut(live["agents"], live["pending_approvals"]),
            "policy": policy,
            "policy_days": ", ".join(calendar.day_abbr[day] for day in sorted(policy.days)),
            # Read only, like the monitoring policy above: no row is created by a page view.
            "shot_policy": ScreenshotPolicy.objects.filter(organization=organization).first(),
            **zone_context(organization),
        }

    return _page(request, "analytics/organization.html", request.user.organization, context)


def _filtered_rows(rows, group):
    """All rows, or only the ones in the chosen monitoring group (from the overview counts)."""
    return employees_in_group(rows, group) if group in MONITORING_GROUPS else rows


@role_required(Role.ADMIN)
def organization_monitoring_export(request):
    """Admin: download the employee monitoring status as CSV. Names, teams and status only, no emails.

    The download is recorded in the audit log with the number of rows, never the names.
    """
    organization = request.user.organization
    people = monitoring_overview(request.user)["employees"]
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="monitoring-status.csv"'
    response["Cache-Control"] = "no-store"
    writer = csv.writer(response)
    writer.writerow(["Employee", "Team", "Monitoring", "Agent status"])
    for person in people:
        writer.writerow([person["name"], person["teams"], MONITORING_GROUPS[person["group"]], person["status"]])
    record_event(
        request.user,
        "monitoring_status.exported",
        organization=organization,
        target=organization,
        detail=f"{len(people)} employees",
    )
    return response


@role_required(Role.SUPER_ADMIN)
def platform_dashboard(request):
    return render(request, "analytics/platform.html", {"report": platform_report(request.user)})


# --- Chart data and live panels (fetched by the pages with HTMX and Chart.js) -------------


def _trend_response(request, employee_ids, widen=True):
    """JSON for a trend chart. Uses the same date range as the page. Team and organization charts widen it
    to two weeks for context; an employee's chart shows exactly the dates selected on its page."""
    organization = request.user.organization
    start, end, _ = _date_range(request, organization)
    if widen:
        start, end = trend_range(start, end)
    return JsonResponse(daily_trend(organization, employee_ids, start, end))


@role_required(Role.EMPLOYEE)
def my_attendance(request):
    """The employee's own attendance by day. Every query is for request.user only."""
    user = request.user

    def context(start, end):
        return {
            "employee": user,
            "attendance": attendance_by_day(user, start, end),
            **zone_context(user.organization),
        }

    return _page(request, "analytics/my_section.html", user.organization, context)


AUDIT_ROWS_LIMIT = 200


def _my_audit_row(entry, employee, zone):
    """One audit entry for the employee's own Audits tab. Other people are shown as 'Administrator', never by name or email."""
    if entry.actor_id is None:
        who = "System"
    elif entry.actor_id == employee.pk:
        who = "You"
    else:
        who = "Administrator"
    return {"time": entry.created_at.astimezone(zone), "action": entry.action, "who": who, "detail": entry.detail}


@role_required(Role.EMPLOYEE)
def my_audits(request):
    """Audit entries about the signed-in employee only: their own actions, changes to their account, and
    actions on their screenshots. Other employees' entries never reach this page."""
    user = request.user
    zone = display_zone()

    def context(start, end):
        start_at, end_at = local_day_bounds(start, end, zone)
        entries = entries_about(user).filter(created_at__gte=start_at, created_at__lt=end_at).order_by("-created_at", "-id")
        rows = [_my_audit_row(entry, user, zone) for entry in entries.select_related("actor")[:AUDIT_ROWS_LIMIT]]
        return {"employee": user, "audits": rows, **zone_context(user.organization)}

    return _page(request, "analytics/my_audits.html", user.organization, context)


@role_required(Role.EMPLOYEE)
def my_trend(request):
    return _trend_response(request, [request.user.pk])


@role_required(Role.ADMIN, Role.MANAGER)
def employee_trend(request, pk):
    if not visible_employees(request.user).filter(pk=pk).exists():
        raise Http404
    return _trend_response(request, [pk], widen=False)


@role_required(Role.ADMIN, Role.MANAGER)
def team_trend(request, pk):
    team = visible_teams(request.user).filter(pk=pk).first()
    if team is None:
        raise Http404
    members = visible_employees(request.user).filter(team_memberships__team=team)
    return _trend_response(request, members.values_list("pk", flat=True))


@role_required(Role.ADMIN)
def organization_trend(request):
    return _trend_response(request, visible_employees(request.user).values_list("pk", flat=True))


@role_required(Role.ADMIN)
def organization_live(request):
    """The auto-refreshing tiles on the Admin dashboard (an HTMX partial)."""
    organization = request.user.organization
    return render(request, "analytics/_live.html", {"live": live_status(request.user), **zone_context(organization)})
