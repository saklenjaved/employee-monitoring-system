"""Reading the daily totals for dashboards. Every function takes the logged-in user and
returns only what that user may see. A function returns None when the user may not see it.

Categories are worked out per employee when data is read, because a team rule changes
the category for that team's employees only. A Manager's figures use every rule that
applies to the employees they see, including rules of teams the Manager is not in.
"""
from collections import defaultdict
from zoneinfo import ZoneInfo
from datetime import datetime, time, timedelta

from django.conf import settings
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncDate
from django.core.paginator import Paginator
from django.utils import timezone
from django.utils.timesince import timesince

from accounts.models import Role, User
from audit.models import AuditLog
from devices.models import Device, MonitoringStart
from devices.services import sync_state, sync_status
from devices.start import STATUS_ACTIVE, STATUS_PAUSED, monitoring_statuses
from monitoring.models import EventType, MonitoringEvent
from monitoring.services import timezone_for
from organizations.models import Organization
from productivity.models import UNCLASSIFIED, Category, ItemType
from productivity.services import Classifier, create_rule
from workforce.models import EmployeeProfile, UserTeam
from workforce.services import visible_employees, visible_teams

from .models import ActivityDaily, ItemUsageDaily

CATEGORY_KEYS = (Category.PRODUCTIVE.value, Category.UNPRODUCTIVE.value, Category.NEUTRAL.value, UNCLASSIFIED)
TOP_ITEMS = 10  # rows in the "top applications" and "top websites" lists
UNCLASSIFIED_DEFAULT_LIMIT = 200  # items unclassified_items returns when no limit is given
UNCLASSIFIED_LIST_LIMIT = 1000  # items on the Unclassified page, which pages through them 25 at a time
UNCLASSIFIED_COUNT_LIMIT = 1000  # the dashboard tile counts at most this many items
UNCLASSIFIED_ROW_LIMIT = 5000  # most employee-and-item rows looked at when finding unclassified items


# --- Building blocks ---------------------------------------------------------------------


def _percent(part, whole):
    return round(100 * part / whole, 1) if whole else None


def _new_stats():
    return {"category_seconds": dict.fromkeys(CATEGORY_KEYS, 0.0), "active_seconds": 0.0, "idle_seconds": 0.0}


def _finish(stats):
    """Add the figures worked out from the sums: application time and the two percentages."""
    stats["app_seconds"] = sum(stats["category_seconds"].values())
    active, idle = stats["active_seconds"], stats["idle_seconds"]
    stats["activity_percent"] = _percent(active, active + idle)
    # Productive share of all application time. Unclassified time counts in the total.
    stats["productive_percent"] = _percent(stats["category_seconds"]["productive"], stats["app_seconds"])
    return stats


def _totals(stats_list):
    total = _new_stats()
    for stats in stats_list:
        for key in CATEGORY_KEYS:
            total["category_seconds"][key] += stats["category_seconds"][key]
        total["active_seconds"] += stats["active_seconds"]
        total["idle_seconds"] += stats["idle_seconds"]
    return _finish(total)


def _teams_of(organization, employee_ids):
    """The team IDs of each employee: {employee_id: [team_id, ...]}."""
    teams_of = defaultdict(list)
    memberships = UserTeam.objects.filter(organization=organization, user_id__in=employee_ids)
    for user_id, team_id in memberships.values_list("user_id", "team_id"):
        teams_of[user_id].append(team_id)
    return teams_of


def _item_rows(organization, employee_ids, start_date, end_date):
    return (
        ItemUsageDaily.objects.filter(
            organization=organization, user_id__in=employee_ids, date__gte=start_date, date__lte=end_date
        )
        .values("user_id", "item_type", "name")
        .annotate(total=Sum("seconds"))
    )


def _add_items(rows, classifier, teams_of, per_employee):
    """Classify every row. Fills the per-employee category totals and returns the item entries."""
    items = {ItemType.APP.value: {}, ItemType.WEBSITE.value: {}}
    for row in rows:
        user_id, item_type, name, total = row["user_id"], row["item_type"], row["name"], row["total"]
        category = classifier.category_for(item_type, name, teams_of[user_id])
        # Website time happens inside the browser, so categories add up application time only.
        if item_type == ItemType.APP:
            per_employee[user_id]["category_seconds"][category] += total
        entry = items[item_type].setdefault(
            name, {"name": name, "seconds": 0.0, "categories": set(), "employees": set()}
        )
        entry["seconds"] += total
        entry["categories"].add(category)
        entry["employees"].add(user_id)
    return items


def _add_activity(organization, employee_ids, start_date, end_date, per_employee):
    activity = (
        ActivityDaily.objects.filter(
            organization=organization, user_id__in=employee_ids, date__gte=start_date, date__lte=end_date
        )
        .values("user_id")
        .annotate(active=Sum("active_seconds"), idle=Sum("idle_seconds"))
    )
    for row in activity:
        per_employee[row["user_id"]]["active_seconds"] = row["active"] or 0.0
        per_employee[row["user_id"]]["idle_seconds"] = row["idle"] or 0.0


def _listing(entries):
    """Turn item entries into a list, busiest first. A mixed category means employees differ."""
    result = [
        {
            "name": entry["name"],
            "category": next(iter(entry["categories"])) if len(entry["categories"]) == 1 else "mixed",
            "seconds": entry["seconds"],
            "employees": len(entry["employees"]),
        }
        for entry in entries.values()
    ]
    return sorted(result, key=lambda item: -item["seconds"])


def _collect(organization, employee_ids, start_date, end_date):
    """Totals for these employees between two dates (inclusive).

    Returns (per_employee, apps, websites): a dict of figures for each employee, and the
    application and website lists with the busiest first.
    """
    employee_ids = list(employee_ids)
    per_employee = {user_id: _new_stats() for user_id in employee_ids}
    items = _add_items(
        _item_rows(organization, employee_ids, start_date, end_date),
        Classifier(organization),
        _teams_of(organization, employee_ids),
        per_employee,
    )
    _add_activity(organization, employee_ids, start_date, end_date, per_employee)
    for stats in per_employee.values():
        _finish(stats)
    return per_employee, _listing(items[ItemType.APP.value]), _listing(items[ItemType.WEBSITE.value])


def _employee_rows(employees, per_employee):
    rows = [{"user": employee, **per_employee[employee.pk]} for employee in employees]
    return sorted(rows, key=lambda row: (-row["app_seconds"], row["user"].full_name.lower()))


def _team_rows(teams, per_employee):
    members = defaultdict(list)
    for team_id, user_id in UserTeam.objects.filter(team__in=teams, user_id__in=list(per_employee)).values_list(
        "team_id", "user_id"
    ):
        members[team_id].append(user_id)
    rows = []
    for team in teams:
        member_ids = members[team.pk]
        rows.append(
            {"team": team, "employee_count": len(member_ids), **_totals([per_employee[i] for i in member_ids])}
        )
    return sorted(rows, key=lambda row: row["team"].name.lower())


# --- Reports for each dashboard ----------------------------------------------------------


def usage_for(viewer, employee, start_date, end_date=None):
    """One employee's usage between two dates (inclusive), or None if `viewer` may not see them.

    Returns {"apps": [...], "websites": [...], "category_seconds": {...}, "app_seconds": n,
             "active_seconds": n, "idle_seconds": n, "activity_percent": n or None,
             "productive_percent": n or None}.
    """
    end_date = end_date or start_date
    if not visible_employees(viewer).filter(pk=employee.pk).exists():
        return None
    per_employee, apps, websites = _collect(employee.organization, [employee.pk], start_date, end_date)
    return {"apps": apps, "websites": websites, **per_employee[employee.pk]}


def team_report(viewer, team_id, start_date, end_date=None):
    """The usage of one team's employees, or None if `viewer` may not see that team."""
    end_date = end_date or start_date
    team = visible_teams(viewer).filter(pk=team_id).first()
    if team is None:
        return None
    employees = list(
        visible_employees(viewer).filter(team_memberships__team=team).order_by("full_name").distinct()
    )
    per_employee, apps, websites = _collect(team.organization, [e.pk for e in employees], start_date, end_date)
    return {
        "team": team,
        "totals": _totals(per_employee.values()),
        "apps": apps[:TOP_ITEMS],
        "websites": websites[:TOP_ITEMS],
        "employees": _employee_rows(employees, per_employee),
    }


def teams_overview(viewer, start_date, end_date=None):
    """One row per team the viewer may see (all teams for an Admin, own teams for a Manager)."""
    end_date = end_date or start_date
    teams = list(visible_teams(viewer))
    if not teams:
        return []
    employees = list(visible_employees(viewer))
    per_employee, _, _ = _collect(viewer.organization, [e.pk for e in employees], start_date, end_date)
    return _team_rows(teams, per_employee)


def agent_status(organization):
    """How many devices are online, offline or revoked."""
    devices = Device.objects.filter(organization=organization)
    cutoff = timezone.now() - timedelta(seconds=settings.DEVICE_ONLINE_SECONDS)
    live = devices.filter(is_active=True)
    online = live.filter(last_seen_at__gte=cutoff).count()
    total_live = live.count()
    return {
        "total": devices.count(),
        "online": online,
        "offline": total_live - online,
        "revoked": devices.filter(is_active=False).count(),
    }


def organization_report(viewer, start_date, end_date=None):
    """The whole organization, for an Admin. Anyone else gets None."""
    end_date = end_date or start_date
    if viewer.role != Role.ADMIN:
        return None
    organization = viewer.organization
    employees = list(visible_employees(viewer).order_by("full_name"))
    ids = [e.pk for e in employees]
    per_employee, apps, websites = _collect(organization, ids, start_date, end_date)

    department_of = dict(
        EmployeeProfile.objects.filter(organization=organization, user_id__in=ids).values_list(
            "user_id", "department__name"
        )
    )
    by_department = defaultdict(list)
    for employee in employees:
        by_department[department_of.get(employee.pk) or "No department"].append(per_employee[employee.pk])
    departments = [
        {"name": name, "employee_count": len(group), **_totals(group)} for name, group in sorted(by_department.items())
    ]
    return {
        "employee_count": len(employees),
        "totals": _totals(per_employee.values()),
        "apps": apps[:TOP_ITEMS],
        "websites": websites[:TOP_ITEMS],
        "teams": _team_rows(list(visible_teams(viewer)), per_employee),
        "departments": departments,
        "employees": _employee_rows(employees, per_employee),
        "unclassified_count": len(unclassified_items(organization, limit=UNCLASSIFIED_COUNT_LIMIT)),
        "agents": agent_status(organization),
    }


def platform_report(viewer):
    """Counts for the whole platform, for the Super Admin. Anyone else gets None.

    It holds counts only. The Super Admin sees no employee-level monitoring data.
    """
    if viewer.role != Role.SUPER_ADMIN:
        return None
    cutoff = timezone.now() - timedelta(seconds=settings.DEVICE_ONLINE_SECONDS)
    live = Q(devices__is_active=True)
    organizations = list(
        Organization.objects.annotate(
            employee_count=Count("users", filter=Q(users__role=Role.EMPLOYEE), distinct=True),
            admin_count=Count("users", filter=Q(users__role=Role.ADMIN), distinct=True),
            device_count=Count("devices", filter=live, distinct=True),
            online_count=Count("devices", filter=live & Q(devices__last_seen_at__gte=cutoff), distinct=True),
        ).order_by("name")
    )
    # Administrative actions by Super Admins and Admins. Employee device events are left out.
    recent = list(
        AuditLog.objects.filter(actor__role__in=[Role.SUPER_ADMIN, Role.ADMIN])
        .select_related("organization")
        .order_by("-created_at", "-id")[:15]
    )
    return {
        "organizations": organizations,
        "organization_count": len(organizations),
        "active_organization_count": sum(1 for org in organizations if org.is_active),
        "inactive_organization_count": sum(1 for org in organizations if not org.is_active),
        "employee_count": sum(org.employee_count for org in organizations),
        "admin_count": sum(org.admin_count for org in organizations),
        "device_count": sum(org.device_count for org in organizations),
        "online_count": sum(org.online_count for org in organizations),
        "recent_activity": recent,
    }


# --- Charts and live figures -------------------------------------------------------------

TREND_MIN_DAYS = 14  # a trend chart shows at least two weeks, even when one day is selected


def trend_range(start_date, end_date):
    """The dates a trend chart covers: the selected range, widened to at least TREND_MIN_DAYS."""
    return min(start_date, end_date - timedelta(days=TREND_MIN_DAYS - 1)), end_date


def daily_trend(organization, employee_ids, start_date, end_date):
    """Hours per category and activity percentage for each day, for a chart.

    The caller has already limited `employee_ids` to what the viewer may see.
    Returns {"labels": ["2026-10-01", ...], "series": {category: [hours, ...]}, "activity_percent": [...]}.
    """
    employee_ids = list(employee_ids)
    days = [start_date + timedelta(days=n) for n in range((end_date - start_date).days + 1)]
    seconds = {day: dict.fromkeys(CATEGORY_KEYS, 0.0) for day in days}
    classifier = Classifier(organization)
    teams_of = _teams_of(organization, employee_ids)
    rows = (
        ItemUsageDaily.objects.filter(
            organization=organization,
            user_id__in=employee_ids,
            item_type=ItemType.APP,
            date__gte=start_date,
            date__lte=end_date,
        )
        .values("user_id", "date", "name")
        .annotate(total=Sum("seconds"))
    )
    for row in rows:
        category = classifier.category_for(ItemType.APP, row["name"], teams_of[row["user_id"]])
        seconds[row["date"]][category] += row["total"]
    activity, idle_seconds = {}, {}
    for row in (
        ActivityDaily.objects.filter(
            organization=organization, user_id__in=employee_ids, date__gte=start_date, date__lte=end_date
        )
        .values("date")
        .annotate(active=Sum("active_seconds"), idle=Sum("idle_seconds"))
    ):
        activity[row["date"]] = _percent(row["active"] or 0, (row["active"] or 0) + (row["idle"] or 0))
        idle_seconds[row["date"]] = row["idle"] or 0
    return {
        "labels": [day.isoformat() for day in days],
        "series": {key: [round(seconds[day][key] / 3600, 2) for day in days] for key in CATEGORY_KEYS},
        "idle_hours": [round(idle_seconds.get(day, 0) / 3600, 2) for day in days],
        "activity_percent": [activity.get(day) for day in days],
    }


EMPLOYEES_TO_CONNECT_LIMIT = 25  # names listed on the Admin dashboard; the count is always exact


def employees_to_connect(viewer):
    """Active employees with no agent connected in the last few minutes. Admin only; None for other roles.

    Returns {"count": n, "people": [up to EMPLOYEES_TO_CONNECT_LIMIT users, by name]}.
    """
    if viewer.role != Role.ADMIN:
        return None
    cutoff = timezone.now() - timedelta(seconds=settings.DEVICE_ONLINE_SECONDS)
    connected = Device.objects.filter(
        organization=viewer.organization, is_active=True, last_seen_at__gte=cutoff
    ).values("user_id")
    waiting = visible_employees(viewer).filter(is_active=True).exclude(pk__in=connected).order_by("full_name")
    return {"count": waiting.count(), "people": list(waiting[:EMPLOYEES_TO_CONNECT_LIMIT])}


LIVE_ROW_LIMIT = 200  # employees shown in the live table; the rest are on the People page
RECENT_ACTIVITY_LIMIT = 20  # application samples in the "recent activity" feed on an employee's page


def recent_activity(employee, limit=RECENT_ACTIVITY_LIMIT):
    """The employee's latest application samples, newest first. Each has the active or idle state sampled with it.

    The times are in the organization's time zone. The caller has already checked that the viewer may see this employee.
    """
    zone = timezone_for(employee.organization)
    events = list(
        MonitoringEvent.objects.filter(
            organization=employee.organization,
            user=employee,
            event_type__in=[EventType.APP_FOCUS, EventType.ACTIVITY_SAMPLE],
        ).order_by("-captured_at", "-id")[: limit * 3]
    )
    state_at = {e.captured_at: e.payload.get("state", "") for e in events if e.event_type == EventType.ACTIVITY_SAMPLE}
    rows = [
        {
            "time": e.captured_at.astimezone(zone),
            "app": e.payload.get("process_name", ""),
            "seconds": e.payload.get("duration_seconds", 0),
            "state": state_at.get(e.captured_at, ""),
        }
        for e in events
        if e.event_type == EventType.APP_FOCUS
    ]
    return rows[:limit]


def employee_live(employee, now=None):
    """What the Admin or Manager sees on an employee's page: the agent's state, plus the latest activity."""
    return {"agent": sync_status(employee, now), "recent": recent_activity(employee)}


def live_employee_rows(viewer, now=None):
    """One row per visible employee: connection state, focused app and window, and queued items.

    The focus is shown only while the PC is online. An offline PC's last focus is not current, so it is hidden.
    """
    now = now or timezone.now()
    employees = list(visible_employees(viewer).order_by("full_name")[:LIVE_ROW_LIMIT])
    latest = {}
    devices = Device.objects.filter(
        organization=viewer.organization, user_id__in=[e.pk for e in employees], is_active=True
    ).order_by(F("last_seen_at").desc(nulls_last=True))
    for device in devices:
        latest.setdefault(device.user_id, device)
    # The real monitoring state of each PC (confirmed start and collecting), not only whether it is online.
    monitoring = monitoring_statuses(latest.values(), now)
    rows = []
    for employee in employees:
        device = latest.get(employee.pk)
        if device is None:
            rows.append({"id": employee.pk, "name": employee.full_name, "state": "not_connected", "monitoring": "not_connected", "focus_app": "", "focus_title": "", "queue_depth": 0, "last_seen": None})
            continue
        state = sync_state(device, now)
        online = state != "offline"
        rows.append(
            {
                "id": employee.pk,
                "name": employee.full_name,
                "state": state,
                "monitoring": monitoring[device.pk],
                "focus_app": device.focus_app if online else "",
                "focus_title": device.focus_title if online else "",
                "queue_depth": device.queue_depth,
                "last_seen": device.last_seen_at,
            }
        )
    return rows


LIVE_ACTIVITY_LIMIT = 8


def live_activity(viewer, limit=LIVE_ACTIVITY_LIMIT):
    """The newest app and website events of the employees the viewer may see. Admin only; an empty list otherwise.

    Each row has kind ("app" or "website"), name (process or domain), who (employee name) and time (in the
    organization's time zone). Only events the agents actually sent are listed.
    """
    if viewer.role != Role.ADMIN:
        return []
    organization = viewer.organization
    zone = timezone_for(organization)
    ids = visible_employees(viewer).values_list("pk", flat=True)
    events = (
        MonitoringEvent.objects.filter(
            organization=organization,
            user_id__in=ids,
            event_type__in=[EventType.APP_FOCUS, EventType.WEBSITE_VISIT],
        )
        .select_related("user")
        .order_by("-captured_at", "-id")[:limit]
    )
    rows = []
    for event in events:
        is_website = event.event_type == EventType.WEBSITE_VISIT
        rows.append(
            {
                "kind": "website" if is_website else "app",
                "name": event.payload.get("domain", "") if is_website else event.payload.get("process_name", ""),
                "who": event.user.full_name,
                "time": event.captured_at.astimezone(zone),
            }
        )
    return rows


def live_status(viewer):
    """Small figures for the auto-refreshing panel on the Admin dashboard. None for other roles."""
    if viewer.role != Role.ADMIN:
        return None
    organization = viewer.organization
    today = timezone.now().astimezone(timezone_for(organization)).date()
    ids = list(visible_employees(viewer).values_list("pk", flat=True))
    today_activity = ActivityDaily.objects.filter(organization=organization, user_id__in=ids, date=today).aggregate(
        active=Sum("active_seconds"), idle=Sum("idle_seconds"), people=Count("user_id", distinct=True)
    )
    employees = live_employee_rows(viewer)
    app_today = dict(
        ItemUsageDaily.objects.filter(organization=organization, user_id__in=ids, item_type=ItemType.APP, date=today)
        .values("user_id")
        .annotate(total=Sum("seconds"))
        .values_list("user_id", "total")
    )
    activity_today = {
        row["user_id"]: (row["active"] or 0, row["idle"] or 0)
        for row in ActivityDaily.objects.filter(organization=organization, user_id__in=ids, date=today)
        .values("user_id")
        .annotate(active=Sum("active_seconds"), idle=Sum("idle_seconds"))
    }
    for row in employees:
        active, idle = activity_today.get(row["id"], (0, 0))
        row["app_seconds_today"] = app_today.get(row["id"], 0)
        row["activity_percent_today"] = _percent(active, active + idle)
    from devices.models import PairingRequest

    people = visible_employees(viewer)
    agents = agent_status(organization)
    live = {
        "employee_count": len(ids),
        "active_count": people.filter(is_active=True).count(),
        "inactive_count": people.filter(is_active=False).count(),
        "not_signed_in_count": people.filter(is_active=True, last_login__isnull=True).count(),
        "registered_count": agents["total"] - agents["revoked"],
        "pending_approvals": PairingRequest.objects.filter(
            organization=organization, status=PairingRequest.Status.PENDING, expires_at__gt=timezone.now()
        ).count(),
        "agents": agents,
        "active_today_seconds": today_activity["active"] or 0,
        "idle_today_seconds": today_activity["idle"] or 0,
        "people_active_today": today_activity["people"],
        "employees": employees,
        "checked_at": timezone.now(),
    }
    # Attention: PCs waiting for approval, devices that stopped reporting, and employees not signed in yet.
    live["attention_count"] = live["pending_approvals"] + live["not_signed_in_count"] + agents["offline"]
    live["monitoring_active_count"] = monitoring_overview(viewer)["active_count"]
    live["activity"] = live_activity(viewer)
    return live


NOT_CONNECTED_LIVE = {"state": "not_connected", "monitoring": "not_connected", "focus_app": "", "focus_title": "", "queue_depth": 0, "last_seen": None}


def monitoring_360_rows(usage_rows, live_rows):
    """Each employee's usage row with their live agent state added, matched by employee id."""
    live = {row["id"]: row for row in live_rows}
    return [{**row, "live": live.get(row["user"].pk, NOT_CONNECTED_LIVE)} for row in usage_rows]


def _monitoring_group(status):
    """Active, Paused or Inactive, from the real agent state of the employee's PC."""
    if status == STATUS_ACTIVE:
        return "active"
    if status == STATUS_PAUSED:
        return "paused"
    return "inactive"


MONITORING_GROUPS = {
    "active": "Being monitored now",
    "paused": "Paused now (outside hours)",
    "inactive": "Not monitored now",
}


def employees_in_group(rows, group):
    """The rows whose live agent state falls in one group (active, paused or inactive)."""
    return [row for row in rows if _monitoring_group(row["live"].get("monitoring")) == group]


def monitoring_overview(viewer, now=None):
    """Who is monitored right now, per employee and per team. Admin only; None for other roles.

    Each employee's state comes from the real agent state of their latest PC (see devices.start). Any status
    other than "monitoring active" or "monitoring paused" counts as Inactive.
    Returns {"active_count", "employees": [...], "teams": [...]}.
    """
    if viewer.role != Role.ADMIN:
        return None
    now = now or timezone.now()
    organization = viewer.organization
    employees = list(visible_employees(viewer).order_by("full_name"))
    ids = {e.pk for e in employees}

    latest = {}
    devices = Device.objects.filter(organization=organization, user_id__in=ids, is_active=True).order_by(
        F("last_seen_at").desc(nulls_last=True)
    )
    for device in devices:
        latest.setdefault(device.user_id, device)
    statuses = monitoring_statuses(latest.values(), now)
    status_of = {user_id: statuses[device.pk] for user_id, device in latest.items()}

    team_names = defaultdict(list)
    team_members = defaultdict(list)
    for user_id, team_id, team_name in UserTeam.objects.filter(organization=organization, user_id__in=ids).values_list(
        "user_id", "team_id", "team__name"
    ):
        team_names[user_id].append(team_name)
        team_members[team_id].append(user_id)

    group_of = {e.pk: _monitoring_group(status_of.get(e.pk)) for e in employees}
    people = [
        {
            "name": e.full_name,
            "teams": ", ".join(sorted(team_names[e.pk])) or "No team",
            "group": group_of[e.pk],
            "status": status_of.get(e.pk, "not_connected"),
        }
        for e in employees
    ]

    teams = []
    for team in visible_teams(viewer):
        members = [user_id for user_id in team_members[team.pk] if user_id in ids]
        monitored = sum(1 for user_id in members if group_of[user_id] == "active")
        teams.append(
            {
                "name": team.name,
                "total": len(members),
                "monitored": monitored,
                "none_monitored": len(members) > 0 and monitored == 0,
            }
        )
    teams.sort(key=lambda row: row["name"].lower())
    active_count = sum(1 for group in group_of.values() if group == "active")
    return {"active_count": active_count, "employees": people, "teams": teams}


# --- Unclassified items ------------------------------------------------------------------


def unclassified_items(organization, days=30, limit=UNCLASSIFIED_DEFAULT_LIMIT):
    """Items from the last `days` days that at least one employee uses with no rule that applies to them.

    "Applies" follows the same rules as the dashboards: an organization rule, or a rule of one of the
    employee's teams. Returns up to `limit` of {"item_type", "name", "seconds", "employees"}, busiest
    first. Only the busiest UNCLASSIFIED_ROW_LIMIT employee-and-item rows are looked at.
    """
    today = timezone.now().astimezone(timezone_for(organization)).date()
    rows = list(
        ItemUsageDaily.objects.filter(organization=organization, date__gte=today - timedelta(days=days))
        .values("user_id", "item_type", "name")
        .annotate(total=Sum("seconds"))
        .order_by("-total")[:UNCLASSIFIED_ROW_LIMIT]
    )
    classifier = Classifier(organization)
    teams_of = _teams_of(organization, {row["user_id"] for row in rows})
    found = {}
    for row in rows:
        if classifier.category_for(row["item_type"], row["name"], teams_of[row["user_id"]]) != UNCLASSIFIED:
            continue
        entry = found.setdefault(
            (row["item_type"], row["name"]),
            {"item_type": row["item_type"], "name": row["name"], "seconds": 0.0, "employees": set()},
        )
        entry["seconds"] += row["total"]
        entry["employees"].add(row["user_id"])
    items = [{**entry, "employees": len(entry["employees"])} for entry in found.values()]
    return sorted(items, key=lambda item: -item["seconds"])[:limit]


def classify_item(actor, organization, item_type, name, category):
    """Give an unclassified item an organization-wide rule."""
    return create_rule(actor, organization, item_type, name, category)


# --- Admin command center (the Admin dashboard's overview cards, alerts and recent activity) ---

# The Recent activity feed: each audit action, the icon it shows, and how it reads.
# Each entry is (icon, status, text). The text uses {who} (who did it), {name} (the employee it is about)
# and {device} (the PC). Statuses: success, pending, warning, active, info.
ACTIVITY_FEED = {
    "user.created_in_admin": ("person", "success", "{who} added employee {name}"),
    "membership.invited": ("mail", "pending", "{who} invited {name}"),
    "user.activated": ("check", "success", "{name} activated their account"),
    "user.deactivated": ("ban", "warning", "{who} deactivated {name}"),
    "device.registered": ("device", "success", "{name} successfully registered WinAgent"),
    "device.pairing_approved": ("device", "success", "{name} approved PC {device}"),
    "device.revoked": ("ban", "warning", "{who} revoked PC {device} of {name}"),
    "monitoring.approval_requested": ("shield", "pending", "{who} approved monitoring for {name}"),
    "monitoring.consented": ("check", "success", "{name} approved monitoring"),
    "monitoring.started": ("play", "active", "Monitoring started successfully for {name}"),
    "employee.updated": ("edit", "info", "{who} updated details for {name}"),
    "user.name_changed": ("edit", "info", "{name} changed their name"),
    "organization.created": ("settings", "info", "{who} created the organization"),
    "team.created": ("settings", "info", "{who} created a team"),
    "classification_rule.created": ("settings", "info", "{who} added a classification rule"),
    "monitoring_policy.changed": ("settings", "info", "{who} changed monitoring settings"),
    "screenshot_policy.changed": ("settings", "info", "{who} changed screenshot settings"),
    "enrollment_token.issued": ("settings", "info", "{who} issued a new enrollment token"),
}
ACTIVITY_STATUS_LABELS = {"success": "Done", "pending": "Waiting", "warning": "Attention", "active": "Active", "info": "Updated"}
RECENT_ACTIVITY_PER_PAGE = 5
DISPLAY_ZONE = ZoneInfo(settings.DISPLAY_TIMEZONE)
RECENT_SCREENSHOT_LIMIT = 5


def _who(actor):
    return (actor.full_name or actor.email) if actor else "System"


def recent_activity_feed(organization, page=1, per_page=RECENT_ACTIVITY_PER_PAGE):
    """One page of the organization's important activities, newest first. Built only from real audit records.

    Returns a Django Page. Each row (an item of the page) has: icon, status, status_label, text, detail,
    when_date, when_time, ago, and `at` (the UTC time). The time is converted to the display time zone here, once.
    """
    entries = AuditLog.objects.filter(organization=organization, action__in=list(ACTIVITY_FEED)).select_related("actor")
    feed_page = Paginator(entries.order_by("-created_at", "-id"), per_page).get_page(page)
    feed_page.object_list = _feed_rows(list(feed_page.object_list))
    return feed_page


def _feed_rows(entries):
    subjects = _activity_subjects(entries)
    rows = []
    for entry in entries:
        icon, status, template = ACTIVITY_FEED[entry.action]
        subject = subjects.get((entry.target_type, entry.target_id), {})
        name = subject.get("name", "an employee")
        text = template.format(who=_who(entry.actor), name=name, device=subject.get("device", "a PC"))
        if entry.action == "user.activated" and entry.actor_id and entry.actor_id != subject.get("user_id"):
            text = f"{_who(entry.actor)} activated {name}"
        local = entry.created_at.astimezone(DISPLAY_ZONE)
        rows.append(
            {
                "icon": icon,
                "status": status,
                "status_label": ACTIVITY_STATUS_LABELS[status],
                "text": text,
                "detail": entry.detail,
                "when_date": f"{local.day} {local:%b %Y}",
                "when_time": local.strftime("%I:%M %p"),
                "ago": timesince(entry.created_at).split(",")[0],
                "at": entry.created_at,
            }
        )
    return rows


def _activity_subjects(entries):
    """Names of the employee, and the PC, each activity is about. Three queries at most, for any number of rows."""
    ids = {"accounts.User": set(), "devices.Device": set(), "devices.MonitoringStart": set()}
    for entry in entries:
        if entry.target_type in ids:
            ids[entry.target_type].add(entry.target_id)
    subjects = {}
    for pk, user_id, full_name, email in User.objects.filter(pk__in=ids["accounts.User"]).values_list(
        "pk", "pk", "full_name", "email"
    ):
        subjects[("accounts.User", str(pk))] = {"user_id": user_id, "name": full_name or email}
    for pk, user_id, full_name, email, device_name in Device.objects.filter(pk__in=ids["devices.Device"]).values_list(
        "pk", "user_id", "user__full_name", "user__email", "name"
    ):
        subjects[("devices.Device", str(pk))] = {"user_id": user_id, "name": full_name or email, "device": device_name}
    for pk, user_id, full_name, email in MonitoringStart.objects.filter(
        pk__in=ids["devices.MonitoringStart"]
    ).values_list("pk", "user_id", "user__full_name", "user__email"):
        subjects[("devices.MonitoringStart", str(pk))] = {"user_id": user_id, "name": full_name or email}
    return subjects


def command_center(viewer, start_date, end_date=None, activity_page=1):
    """Organization figures for the Admin dashboard. Admin only; None for other roles.

    Every figure comes from a database query. Nothing here is estimated.
    """
    from devices.models import MonitoringStart, PairingRequest
    from devices.start import STATUS_ACTIVE, STATUS_PAUSED, monitoring_statuses
    from screenshots.models import Screenshot, ScreenshotStatus

    if viewer.role != Role.ADMIN:
        return None
    end_date = end_date or start_date
    organization = viewer.organization
    zone = timezone_for(organization)
    now = timezone.now()
    cutoff = now - timedelta(seconds=settings.DEVICE_ONLINE_SECONDS)
    day_start = datetime.combine(start_date, time.min, tzinfo=zone)
    day_end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=zone)

    employees = visible_employees(viewer)
    active_ids = employees.filter(is_active=True).values("pk")
    devices = Device.objects.filter(organization=organization)
    agents = agent_status(organization)
    # Monitored means a start the agent confirmed and an agent that reports it is collecting (not just online).
    online = list(devices.filter(is_active=True, user__in=active_ids, last_seen_at__gte=cutoff))
    statuses = monitoring_statuses(online, now)
    monitored_now = len({d.user_id for d in online if statuses[d.pk] == STATUS_ACTIVE})
    paused_now = len({d.user_id for d in online if statuses[d.pk] == STATUS_PAUSED})
    active_count = employees.filter(is_active=True).count()
    screenshots = Screenshot.objects.filter(organization=organization, captured_at__gte=day_start, captured_at__lt=day_end)
    per_day = dict(
        screenshots.annotate(day=TruncDate("captured_at", tzinfo=zone)).values("day").annotate(n=Count("id")).values_list("day", "n")
    )
    labels = [start_date + timedelta(days=n) for n in range((end_date - start_date).days + 1)]
    recent_screenshots = list(
        screenshots.select_related("user").order_by("-captured_at", "-id")[:RECENT_SCREENSHOT_LIMIT]
    )
    activity = recent_activity_feed(organization, page=activity_page)
    monitoring_starts = MonitoringStart.objects.filter(
        organization=organization, requested_at__gte=day_start, requested_at__lt=day_end
    )
    return {
        "employees": {
            "total": employees.count(),
            "active": active_count,
            "inactive": employees.filter(is_active=False).count(),
            "not_signed_in": employees.filter(is_active=True, last_login__isnull=True).count(),
        },
        "devices": {
            "registered": agents["total"] - agents["revoked"],
            "online": agents["online"],
            "offline": agents["offline"],
            "revoked": agents["revoked"],
            "pending_approval": PairingRequest.objects.filter(
                organization=organization, status=PairingRequest.Status.PENDING, expires_at__gt=now
            ).count(),
            "with_errors": devices.filter(is_active=True).exclude(last_error_code="").count(),
        },
        "monitoring": {
            "monitored_now": monitored_now,
            "paused_now": paused_now,
            "not_monitored_now": max(active_count - monitored_now - paused_now, 0),
            "starts_requested": monitoring_starts.count(),
            "starts_confirmed": monitoring_starts.filter(status=MonitoringStart.Status.CONFIRMED).count(),
        },
        "screenshots": {
            "in_range": screenshots.count(),
            "uploaded_in_range": screenshots.filter(status=ScreenshotStatus.UPLOADED).count(),
            "failed_in_range": screenshots.filter(status=ScreenshotStatus.FAILED).count(),
            "per_day": {"labels": [d.isoformat() for d in labels], "counts": [per_day.get(d, 0) for d in labels]},
            "recent": recent_screenshots,
        },
        "activity": activity,
    }
