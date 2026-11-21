"""Builds the daily totals from the raw events (TAD 9, 12).

How time is counted
-------------------
An `app_focus` or `activity_sample` event says: "at `captured_at`, this was true, and the
sample covers `duration_seconds` / `sample_interval_seconds`". The covered time is taken as
the interval that ENDS at `captured_at` (default, to confirm).

Every second of an employee's day is counted once (PRD section 13):
- Application time: if two intervals overlap (a resent event, an agent restart, or two
  devices in use at the same time), the overlap is credited to the interval that started first.
- Active time wins over idle time when devices disagree.
- Websites are counted on their own and are not added to application time.

A day is a calendar day in the organization's time zone. An interval that crosses midnight
is cut at midnight.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models.functions import TruncHour
from django.utils import timezone

from accounts.models import User
from monitoring.models import EventType, MonitoringEvent, MonitoringPolicy
from monitoring.services import get_policy
from monitoring.validation import MAX_DURATION_SECONDS
from productivity.models import ItemType

from .models import ActivityDaily, ItemUsageDaily

# An event captured shortly after midnight can cover time before it. Events are checked on arrival
# to cover at most MAX_DURATION_SECONDS, so this is how far past the day's end to look.
LOOKAHEAD = timedelta(seconds=MAX_DURATION_SECONDS)

# At most this many different applications, and this many websites, are kept for one employee and
# day (the busiest ones). It stops a faulty or hostile device from creating unlimited rows.
MAX_ITEMS_PER_DAY = 300


def day_bounds(tz, day):
    """Start and end of a local calendar day, as UTC datetimes."""
    start = datetime.combine(day, time.min, tzinfo=tz).astimezone(dt_timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(dt_timezone.utc)
    return start, end


def _interval(event, seconds_key, day_start, day_end):
    """(start, end) of the time an event covers, cut to the day, or None if nothing is left."""
    seconds = event["payload"].get(seconds_key)
    if not seconds:
        return None
    end = event["captured_at"]
    start = end - timedelta(seconds=seconds)
    start, end = max(start, day_start), min(end, day_end)
    return (start, end) if end > start else None


def _count_once(intervals):
    """Seconds per key with every moment counted once. `intervals` are (start, end, key)."""
    totals = defaultdict(float)
    covered_until = None
    for start, end, key in sorted(intervals, key=lambda item: (item[0], item[1])):
        if covered_until is not None and end <= covered_until:
            continue  # completely inside time that is already counted
        counted_from = start if covered_until is None else max(start, covered_until)
        totals[key] += (end - counted_from).total_seconds()
        covered_until = end
    return totals


def _union(intervals):
    """Merge overlapping (start, end) intervals into separate ones."""
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _subtract(intervals, taken):
    """The parts of `intervals` that are not inside `taken`. Both are merged lists."""
    result = []
    for start, end in intervals:
        cursor = start
        for taken_start, taken_end in taken:
            if taken_end <= cursor or taken_start >= end:
                continue
            if taken_start > cursor:
                result.append((cursor, taken_start))
            cursor = max(cursor, taken_end)
        if cursor < end:
            result.append((cursor, end))
    return result


def _length(intervals):
    return sum((end - start).total_seconds() for start, end in intervals)


def _events_for(user, day_start, day_end):
    rows = MonitoringEvent.objects.filter(
        organization_id=user.organization_id,
        user=user,
        captured_at__gte=day_start,
        captured_at__lt=day_end + LOOKAHEAD,
    ).values("event_type", "captured_at", "payload")
    return list(rows)


def _busiest(items):
    """Keep the busiest MAX_ITEMS_PER_DAY applications and websites. `items` maps (type, name) to seconds."""
    kept = {}
    for item_type in (ItemType.APP.value, ItemType.WEBSITE.value):
        of_type = sorted(((seconds, name) for (kind, name), seconds in items.items() if kind == item_type), reverse=True)
        for seconds, name in of_type[:MAX_ITEMS_PER_DAY]:
            kept[(item_type, name)] = seconds
    return kept


def compute_day(events, day_start, day_end):
    """Totals for one day from a list of event dicts. Returns (item_seconds, active, idle)."""
    app_intervals, site_intervals, active, idle = [], [], [], []
    for event in events:
        kind, payload = event["event_type"], event["payload"]
        if kind == EventType.APP_FOCUS:
            span = _interval(event, "duration_seconds", day_start, day_end)
            if span:
                app_intervals.append((*span, payload["process_name"].lower()))
        elif kind == EventType.WEBSITE_VISIT:
            span = _interval(event, "duration_seconds", day_start, day_end)
            if span:
                site_intervals.append((*span, payload["domain"].lower()))
        elif kind == EventType.ACTIVITY_SAMPLE:
            span = _interval(event, "sample_interval_seconds", day_start, day_end)
            if span:
                (active if payload["state"] == "active" else idle).append(span)

    items = {}
    for name, seconds in _count_once(app_intervals).items():
        items[(ItemType.APP.value, name)] = seconds
    for name, seconds in _count_once(site_intervals).items():
        items[(ItemType.WEBSITE.value, name)] = seconds
    active_union = _union(active)
    idle_only = _subtract(_union(idle), active_union)
    return items, _length(active_union), _length(idle_only)


def rebuild_day(user, day):
    """Recalculate one employee's totals for one local day. Safe to run again at any time."""
    tz = ZoneInfo(get_policy(user.organization).timezone)
    day_start, day_end = day_bounds(tz, day)
    items, active_seconds, idle_seconds = compute_day(_events_for(user, day_start, day_end), day_start, day_end)
    items = _busiest(items)
    with transaction.atomic():
        ItemUsageDaily.objects.filter(user=user, date=day).delete()
        ActivityDaily.objects.filter(user=user, date=day).delete()
        ItemUsageDaily.objects.bulk_create(
            ItemUsageDaily(
                organization=user.organization, user=user, date=day, item_type=item_type, name=name, seconds=seconds
            )
            for (item_type, name), seconds in items.items()
        )
        if active_seconds or idle_seconds:
            ActivityDaily.objects.create(
                organization=user.organization,
                user=user,
                date=day,
                active_seconds=active_seconds,
                idle_seconds=idle_seconds,
            )


def rebuild_recent(since_hours=72):
    """Recalculate every employee-day touched by events RECEIVED recently.

    Late events (sent from an offline queue) change old days, so this goes by when the
    event arrived, not when it was captured. Returns the number of employee-days rebuilt.
    """
    cutoff = timezone.now() - timedelta(hours=since_hours)
    # One row per employee and hour is enough to know which days to rebuild.
    recent = list(
        MonitoringEvent.objects.filter(received_at__gte=cutoff)
        .annotate(hour=TruncHour("captured_at"))
        .values_list("user_id", "hour")
        .distinct()
    )
    users = User.objects.filter(pk__in={user_id for user_id, _ in recent}).select_related("organization")
    users = {user.pk: user for user in users}
    zone_names = dict(
        MonitoringPolicy.objects.filter(organization_id__in={u.organization_id for u in users.values()}).values_list(
            "organization_id", "timezone"
        )
    )
    wanted = set()
    for user_id, hour in recent:
        tz = ZoneInfo(zone_names.get(users[user_id].organization_id, "UTC"))
        # An event in this hour changes its own day. It may also cover time before midnight, so the
        # day before it can change too. Local midnight can fall inside the hour, so check both ends.
        for moment in (hour, hour + timedelta(minutes=59, seconds=59)):
            wanted.add((user_id, moment.astimezone(tz).date()))
            wanted.add((user_id, (moment - LOOKAHEAD).astimezone(tz).date()))
    for user_id, day in sorted(wanted):
        rebuild_day(users[user_id], day)
    return len(wanted)
