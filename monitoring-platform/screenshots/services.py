"""Screenshot rules: settings, exclusions, agent upload flow, and who may look at images.

Follows the order in TAD section 7 and the rules in .claude/rules/screenshot-policy.md. Image bytes
never pass through here: the agent uploads straight to storage, and people are sent to short-lived
signed links.
"""
from datetime import datetime, time, timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from accounts.models import Role
from audit.services import record_event
from monitoring.services import is_within_monitoring_hours, timezone_for
from workforce.models import UserTeam
from workforce.services import visible_employees

from .models import DEFAULT_MONITORING_MINUTES, Screenshot, ScreenshotExclusion, ScreenshotPolicy, ScreenshotStatus
from .storage import get_storage

POLICY_FIELDS = (
    "enabled",
    "interval_seconds",
    "retention_days",
    "blur_level",
    "monitoring_minutes",
    "managers_can_view",
    "managers_can_download",
)
MAX_CLOCK_AHEAD = timedelta(minutes=10)
MAX_CAPTURE_AGE = timedelta(hours=24)


class ScreenshotDenied(Exception):
    """The policy does not allow a screenshot now. The reason is deliberately not given."""


class ScreenshotConflict(Exception):
    """This screenshot was already finished."""


class ObjectMissing(Exception):
    """The agent says it uploaded the image, but it is not in storage."""


class NotAllowed(Exception):
    """The person may not do this with the screenshot."""


# --- Settings and exclusions ------------------------------------------------------------


def get_policy(organization):
    policy, _ = ScreenshotPolicy.objects.get_or_create(organization=organization)
    return policy


def monitoring_seconds(organization):
    """How long one monitoring start may collect, from the Admin's setting. The starting value if none is saved."""
    minutes = (
        ScreenshotPolicy.objects.filter(organization=organization).values_list("monitoring_minutes", flat=True).first()
    )
    return (minutes or DEFAULT_MONITORING_MINUTES) * 60


def update_policy(actor, organization, values):
    """Save new settings. Screenshots can only be switched on once the interval, retention and blur
    are all chosen. Nothing is written if nothing changed."""
    get_policy(organization)  # make sure the row exists before it is locked
    with transaction.atomic():
        policy = ScreenshotPolicy.objects.select_for_update().get(organization=organization)
        changes = []
        for field in POLICY_FIELDS:
            new_value = values.get(field, getattr(policy, field))  # a missing value means "unchanged"
            old_value = getattr(policy, field)
            if old_value != new_value:
                setattr(policy, field, new_value)
                changes.append(f"{field}: {old_value}->{new_value}")
        if policy.enabled and not policy.is_configured:
            raise ValueError("Choose the interval, retention and blur level before switching screenshots on.")
        if changes:
            policy.version += 1
            policy.save()
            record_event(
                actor,
                "screenshot_policy.changed",
                organization=organization,
                target=policy,
                detail=f"version {policy.version}; " + "; ".join(changes)[:250],
            )
    return policy


def _bump_version(organization):
    get_policy(organization)  # make sure the row exists
    # The add happens in the database, so two changes at once cannot both write the same number.
    ScreenshotPolicy.objects.filter(organization=organization).update(version=F("version") + 1)


def add_exclusion(actor, organization, team=None, user=None):
    if (team is None) == (user is None):
        raise ValueError("Exclude either a team or an employee.")
    target = team or user
    if target.organization_id != organization.id:
        raise ValueError("That belongs to a different organization.")
    with transaction.atomic():
        exclusion = ScreenshotExclusion.objects.create(organization=organization, team=team, user=user)
        _bump_version(organization)
        record_event(actor, "screenshot_exclusion.added", organization=organization, target=exclusion)
    return exclusion


def remove_exclusion(actor, exclusion):
    with transaction.atomic():
        organization, exclusion_id = exclusion.organization, exclusion.pk
        exclusion.delete()
        _bump_version(organization)
        record_event(
            actor, "screenshot_exclusion.removed", organization=organization, detail=f"exclusion {exclusion_id}"
        )


def is_excluded(user):
    """True if the employee, or any team they are in, is excluded from screenshots."""
    team_ids = UserTeam.objects.filter(user=user, organization=user.organization).values("team_id")
    exclusions = ScreenshotExclusion.objects.filter(organization=user.organization)
    return exclusions.filter(Q(user=user) | Q(team_id__in=team_ids)).exists()


def agent_screenshot_policy(device):
    """The screenshots part of the policy that a device downloads (docs/api-contract.yaml)."""
    policy = ScreenshotPolicy.objects.filter(organization=device.organization).first()
    if policy is None or not policy.is_active or is_excluded(device.user):
        return {"enabled": False}
    return {"enabled": True, "interval_seconds": policy.interval_seconds, "blur_level": policy.blur_level}


def version_offset(organization):
    """How much the screenshot settings add to the policy version. Zero until they are first changed,
    so the version only ever goes up when either part changes."""
    version = ScreenshotPolicy.objects.filter(organization=organization).values_list("version", flat=True).first()
    return (version or 1) - 1


# --- Upload flow (agent) ----------------------------------------------------------------


def authorize_upload(device, data, now=None):
    """Check the policy again on the server and, if it allows, create the record and a signed upload link.

    Returns (screenshot, upload_url, upload_headers, expires_at). Raises ScreenshotDenied, ScreenshotConflict
    or ValueError (bad data).
    """
    now = now or timezone.now()
    organization = device.organization
    policy = ScreenshotPolicy.objects.filter(organization=organization).first()
    if policy is None or not policy.is_active:
        raise ScreenshotDenied()
    if not is_within_monitoring_hours(organization, now):
        raise ScreenshotDenied()
    if is_excluded(device.user):
        raise ScreenshotDenied()
    captured_at = data["captured_at"]
    if captured_at > now + MAX_CLOCK_AHEAD or captured_at < now - MAX_CAPTURE_AGE:
        raise ValueError("captured_at is too far from the current time")

    shot = Screenshot.objects.filter(
        organization=organization, device=device, screenshot_id=data["screenshot_id"]
    ).first()
    if shot is not None and shot.status != ScreenshotStatus.AUTHORIZED:
        raise ScreenshotConflict()
    if shot is None:
        try:
            with transaction.atomic():
                shot = Screenshot.objects.create(
                    organization=organization,
                    user=device.user,
                    device=device,
                    screenshot_id=data["screenshot_id"],
                    object_key=Screenshot.new_object_key(organization.pk, captured_at),
                    captured_at=captured_at,
                    byte_size=data["byte_size"],
                    sha256=data["sha256"],
                    width=data.get("width"),
                    height=data.get("height"),
                    redaction_applied=data.get("redaction_applied"),
                )
        except IntegrityError:  # the same request arrived twice at once
            shot = Screenshot.objects.get(
                organization=organization, device=device, screenshot_id=data["screenshot_id"]
            )
    seconds = settings.SCREENSHOT_UPLOAD_LINK_SECONDS
    url, headers = get_storage().presign_upload(shot.object_key, shot.byte_size, shot.sha256, seconds)
    return shot, url, headers, now + timedelta(seconds=seconds)


def complete_upload(device, screenshot_id, status, error_code=""):
    """The agent reports the result. Returns the screenshot, or None if this device has no such screenshot."""
    with transaction.atomic():
        shot = (
            Screenshot.objects.select_for_update()
            .filter(organization=device.organization, device=device, screenshot_id=screenshot_id)
            .first()
        )
        if shot is None:
            return None
        if shot.status != ScreenshotStatus.AUTHORIZED:
            return shot  # already finished: reporting again changes nothing
        if status == "uploaded":
            if get_storage().object_size(shot.object_key) != shot.byte_size:
                raise ObjectMissing()
            shot.status = ScreenshotStatus.UPLOADED
            action, detail = "screenshot.uploaded", ""
        else:
            shot.status = ScreenshotStatus.FAILED
            shot.error_code = error_code or "upload_failed"
            action, detail = "screenshot.upload_failed", shot.error_code
        shot.completed_at = timezone.now()
        shot.save(update_fields=["status", "error_code", "completed_at"])
        record_event(device.user, action, organization=shot.organization, target=shot, detail=detail)
    if shot.status == ScreenshotStatus.FAILED:
        get_storage().delete(shot.object_key)  # nothing is kept from a failed upload
    return shot


# --- Who may look at screenshots ---------------------------------------------------------


def can_view_screenshots(viewer):
    """An Admin may look at screenshots. A Manager may only if the organization allows it. Others may not."""
    if viewer.role == Role.ADMIN:
        return True
    if viewer.role == Role.MANAGER:
        policy = ScreenshotPolicy.objects.filter(organization=viewer.organization).first()
        return bool(policy and policy.managers_can_view)
    return False


def visible_screenshots(viewer):
    """The screenshots `viewer` may see. An Admin sees the whole organization. A Manager sees the
    employees of their teams, if the organization allows Managers to view. Others see nothing."""
    shots = Screenshot.objects.select_related("user", "device").filter(organization=viewer.organization)
    if not can_view_screenshots(viewer):
        return shots.none()
    if viewer.role == Role.ADMIN:
        return shots
    return shots.filter(user__in=visible_employees(viewer))


def get_visible_screenshot(viewer, pk):
    return visible_screenshots(viewer).filter(pk=pk).first()


def can_download(viewer):
    """An Admin may download. A Manager may only if the organization has switched that on."""
    if viewer.role == Role.ADMIN:
        return True
    if viewer.role == Role.MANAGER:
        policy = ScreenshotPolicy.objects.filter(organization=viewer.organization).first()
        return bool(policy and policy.managers_can_view and policy.managers_can_download)
    return False


def can_delete(viewer):
    """Only an Admin may delete a screenshot. Managers and employees may not."""
    return viewer.role == Role.ADMIN


def delete_screenshot(actor, shot):
    """Admin only. Removes the record, then its image. The audit entry holds the record ID only.

    The record is removed in one transaction with its audit entry. The image is removed after that, so a
    storage problem never leaves a record that points to nothing it can explain.
    """
    if not can_delete(actor) or actor.organization_id != shot.organization_id:
        raise NotAllowed()
    organization, object_key = shot.organization, shot.object_key
    with transaction.atomic():
        record_event(actor, "screenshot.deleted", organization=organization, target=shot, detail=f"screenshot {shot.pk}")
        shot.delete()
    get_storage().delete(object_key)


def view_link(viewer, shot):
    """A short-lived link to see the image. Every call is written to the audit log."""
    if shot.status != ScreenshotStatus.UPLOADED:
        raise NotAllowed()
    with transaction.atomic():
        record_event(viewer, "screenshot.viewed", organization=shot.organization, target=shot)
    return get_storage().presign_download(shot.object_key, settings.SCREENSHOT_VIEW_LINK_SECONDS)


def download_link(viewer, shot):
    """A short-lived link that saves the image as a file. Needs download permission, and is audited."""
    if shot.status != ScreenshotStatus.UPLOADED or not can_download(viewer):
        raise NotAllowed()
    with transaction.atomic():
        record_event(viewer, "screenshot.downloaded", organization=shot.organization, target=shot)
    filename = f"screenshot-{shot.pk}-{shot.captured_at:%Y%m%d-%H%M%S}.png"  # no names or emails
    return get_storage().presign_download(shot.object_key, settings.SCREENSHOT_VIEW_LINK_SECONDS, filename)


def day_range(organization, start_date, end_date):
    """UTC start and end of a range of local days (the organization's time zone)."""
    tz = timezone_for(organization)
    start = datetime.combine(start_date, time.min, tzinfo=tz)
    end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=tz)
    return start, end


# --- Cleanup ------------------------------------------------------------------------------


def give_up_unfinished(now=None):
    """Mark uploads that were authorized but never finished as failed and remove any partial object."""
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=settings.SCREENSHOT_UPLOAD_GIVE_UP_SECONDS)
    stale = Screenshot.objects.filter(status=ScreenshotStatus.AUTHORIZED, created_at__lt=cutoff)
    count = 0
    for shot in stale:
        get_storage().delete(shot.object_key)
        Screenshot.objects.filter(pk=shot.pk).update(
            status=ScreenshotStatus.FAILED, error_code="upload_expired", completed_at=now
        )
        count += 1
    return count


def delete_expired(now=None):
    """Delete screenshots older than each organization's retention period. Returns the number deleted.

    An organization with no retention period set loses nothing.
    """
    now = now or timezone.now()
    total = 0
    for policy in ScreenshotPolicy.objects.exclude(retention_days__isnull=True).select_related("organization"):
        cutoff = now - timedelta(days=policy.retention_days)
        old = list(Screenshot.objects.filter(organization=policy.organization, captured_at__lt=cutoff))
        if not old:
            continue
        for shot in old:
            get_storage().delete(shot.object_key)
        with transaction.atomic():
            Screenshot.objects.filter(pk__in=[shot.pk for shot in old]).delete()
            record_event(
                None,
                "screenshot.retention_deleted",
                organization=policy.organization,
                detail=f"{len(old)} older than {policy.retention_days} days",
            )
        total += len(old)
    return total
