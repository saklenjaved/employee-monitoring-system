from zoneinfo import ZoneInfo

from django.db import transaction

from audit.services import record_event

from .models import MonitoringPolicy

POLICY_FIELDS = (
    "enabled",
    "days",
    "start_time",
    "end_time",
    "sample_interval_seconds",
    "idle_threshold_seconds",
    "sync_interval_seconds",
    "timezone",
)


def get_policy(organization):
    """Return the organization's policy. It is created with defaults the first time."""
    policy, _ = MonitoringPolicy.objects.get_or_create(organization=organization)
    return policy


def timezone_for(organization):
    """The organization's time zone, without creating a policy row (safe to call on a page view)."""
    name = MonitoringPolicy.objects.filter(organization=organization).values_list("timezone", flat=True).first()
    return ZoneInfo(name or "UTC")


def is_within_monitoring_hours(organization, moment):
    """True if `moment` is inside the organization's monitoring days and hours (its time zone).

    The rule is the same as the agent's: enabled, an allowed weekday, and a time from the start
    up to (not including) the end. An end before the start means the window crosses midnight.
    """
    policy = get_policy(organization)
    if not policy.enabled:
        return False
    local = moment.astimezone(ZoneInfo(policy.timezone))
    if local.weekday() not in policy.days:
        return False
    now_time = local.time().replace(tzinfo=None)
    if policy.start_time <= policy.end_time:
        return policy.start_time <= now_time < policy.end_time
    return now_time >= policy.start_time or now_time < policy.end_time


def update_policy(actor, organization, values):
    """Save new values. Nothing is written if nothing changed."""
    get_policy(organization)  # make sure the row exists before it is locked
    with transaction.atomic():
        # The lock makes two Admins saving at once take turns, so versions are never repeated.
        policy = MonitoringPolicy.objects.select_for_update().get(organization=organization)
        changes = []
        for field in POLICY_FIELDS:
            new_value = values.get(field, getattr(policy, field))  # a missing value means "unchanged"
            if field == "days":
                new_value = sorted(int(day) for day in new_value)
            old_value = getattr(policy, field)
            if old_value != new_value:
                setattr(policy, field, new_value)
                changes.append(f"{field}: {old_value}->{new_value}")
        if changes:
            policy.version += 1
            policy.save()
            detail = f"version {policy.version}; " + "; ".join(changes)
            record_event(
                actor,
                "monitoring_policy.changed",
                organization=organization,
                target=policy,
                detail=detail[:300],
            )
    return policy


def policy_for_agent(organization):
    """The policy as the Windows agent expects it (docs/api-contract.yaml, PolicyResponse)."""
    policy = get_policy(organization)
    return {
        "policy_version": str(policy.version),
        "monitoring": {
            "enabled": policy.enabled,
            "days": sorted(policy.days),
            "start": policy.start_time.strftime("%H:%M"),
            "end": policy.end_time.strftime("%H:%M"),
            # The hours are in this time zone, the same one the server uses before it accepts a screenshot.
            "timezone": policy.timezone,
        },
        "activity": {
            "sample_interval_seconds": policy.sample_interval_seconds,
            "idle_threshold_seconds": policy.idle_threshold_seconds,
            "sync_interval_seconds": policy.sync_interval_seconds,
        },
        # Placeholder. The devices layer replaces it with the real screenshot settings for each device.
        "screenshots": {"enabled": False},
        "exclusions": {"apps": [], "blur_regions": []},
    }
