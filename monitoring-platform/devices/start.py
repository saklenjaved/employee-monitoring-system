"""Starting monitoring from the employee's dashboard.

The PIN is checked in accounts.pin before anything here runs. This file creates the start request,
hands it to the agent through the heartbeat, and records the agent's confirmation. Monitoring
counts as started only when the agent reports it is collecting under that request.
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from audit.services import record_event

from .models import Device, MonitoringStart

STATUS_NOT_CONNECTED = "not_connected"
STATUS_READY = "ready_to_start"
STATUS_REQUESTED = "start_requested"  # a start is waiting, but the PC has not been seen lately
STATUS_STARTING = "starting"  # a start is waiting and the PC is online; the agent picks it up at its next check-in
STATUS_ACTIVE = "monitoring_active"
STATUS_PAUSED = "monitoring_paused"  # the agent accepted the start but is not collecting (outside hours, remote session)
STATUS_OFFLINE = "device_offline"
STATUS_ERROR = "error"
STATUS_AWAITING = "awaiting_consent"  # an Admin approved; the employee has not confirmed yet

# What the agent reports while it holds a start and collects (only "active"), or holds one but must not collect.
AGENT_COLLECTING = "active"
AGENT_PAUSED_STATES = ("paused_hours", "unsupported_session")
# The agent confirms a start in any of these states: it has the start and follows it.
AGENT_ACCEPTING_STATES = (AGENT_COLLECTING, *AGENT_PAUSED_STATES)


def _pc_for(user):
    """The employee's most recently seen working device, or None."""
    return (
        Device.objects.filter(user=user, organization=user.organization, is_active=True)
        .order_by(F("last_seen_at").desc(nulls_last=True), "-registered_at")
        .first()
    )


def request_start(user, now=None, actor=None):
    """Create a pending start for the employee's PC. Returns (start, None) or (None, reason).

    `actor` is who asked: the employee by default, or the Admin who clicked Start Monitoring.
    """
    now = now or timezone.now()
    device = _pc_for(user)
    if device is None:
        return None, "no_pc"
    with transaction.atomic():
        pending = MonitoringStart.objects.select_for_update().filter(device=device, status=MonitoringStart.Status.PENDING).first()
        if pending and pending.expires_at > now:
            return pending, None  # a start is already waiting; do not make a second one
        if pending:
            pending.status = MonitoringStart.Status.EXPIRED
            pending.save(update_fields=["status"])
        start = MonitoringStart.objects.create(
            organization=user.organization,
            user=user,
            device=device,
            expires_at=now + timedelta(seconds=settings.MONITORING_START_TIMEOUT_SECONDS),
        )
        record_event(actor or user, "monitoring.start_requested", organization=user.organization, target=start)
    return start, None


def request_approval(person, actor, now=None):
    """Admin: ask the employee to confirm monitoring on their installed PC. Returns (start, None) or (None, reason).

    This does not start anything. The agent only gets a start after the employee confirms (give_consent).
    """
    now = now or timezone.now()
    device = _pc_for(person)
    if device is None:
        return None, "no_pc"
    with transaction.atomic():
        # A request the employee never answered runs out. Close it so a new click makes a fresh one.
        MonitoringStart.objects.filter(
            device=device, status=MonitoringStart.Status.AWAITING_CONSENT, expires_at__lte=now
        ).update(status=MonitoringStart.Status.EXPIRED)
        existing = (
            MonitoringStart.objects.select_for_update()
            .filter(device=device, status__in=[MonitoringStart.Status.AWAITING_CONSENT, MonitoringStart.Status.PENDING])
            .first()
        )
        if existing is not None:
            return existing, None  # a request is already waiting; do not make a second one
        start = MonitoringStart.objects.create(
            organization=person.organization,
            user=person,
            device=device,
            status=MonitoringStart.Status.AWAITING_CONSENT,
            expires_at=now + timedelta(seconds=settings.MONITORING_APPROVAL_TIMEOUT_SECONDS),
        )
        record_event(actor, "monitoring.approval_requested", organization=person.organization, target=start)
    return start, None


def give_consent(user, now=None):
    """Employee: confirm monitoring on their own PC, after an Admin approved it. Returns (start, None) or (None, reason).

    Reasons: "no_pc", "not_registered" (the PC has not connected yet), "not_approved" (no Admin approval),
    "already_started" (a start is already running; not an error, the button was pressed twice).
    """
    now = now or timezone.now()
    device = _pc_for(user)
    if device is None:
        return None, "no_pc"
    with transaction.atomic():
        awaiting = (
            MonitoringStart.objects.select_for_update()
            .filter(device=device, status=MonitoringStart.Status.AWAITING_CONSENT, expires_at__gt=now)
            .first()
        )
        if awaiting is None:
            running = (
                MonitoringStart.objects.filter(device=device, status=MonitoringStart.Status.PENDING, expires_at__gt=now).exists()
                or MonitoringStart.objects.filter(
                    device=device, status=MonitoringStart.Status.CONFIRMED, authorized_until__gt=now
                ).exists()
            )
            return None, "already_started" if running else "not_approved"
        if device.last_seen_at is None:
            return None, "not_registered"
        awaiting.status = MonitoringStart.Status.PENDING
        awaiting.expires_at = now + timedelta(seconds=settings.MONITORING_START_TIMEOUT_SECONDS)
        awaiting.save(update_fields=["status", "expires_at"])
        record_event(user, "monitoring.consented", organization=user.organization, target=awaiting)
    return awaiting, None


def start_instruction(device, now=None):
    """What the heartbeat response tells the agent: a pending start, a running one, or nothing."""
    now = now or timezone.now()
    pending = MonitoringStart.objects.filter(
        device=device, status=MonitoringStart.Status.PENDING, expires_at__gt=now
    ).first()
    if pending:
        return {"id": str(pending.id), "authorized_seconds": settings.MONITORING_AUTHORIZED_SECONDS}
    running = (
        MonitoringStart.objects.filter(device=device, status=MonitoringStart.Status.CONFIRMED, authorized_until__gt=now)
        .order_by("-confirmed_at")
        .first()
    )
    if running:
        remaining = int((running.authorized_until - now).total_seconds())
        return {"id": str(running.id), "authorized_seconds": remaining}
    return None


def confirm_start(device, start_id, now=None):
    """Mark a pending start confirmed when the agent reports it is collecting under that request."""
    now = now or timezone.now()
    with transaction.atomic():
        start = (
            MonitoringStart.objects.select_for_update()
            .filter(id=start_id, device=device, status=MonitoringStart.Status.PENDING, expires_at__gt=now)
            .first()
        )
        if start is None:
            return False
        start.status = MonitoringStart.Status.CONFIRMED
        start.confirmed_at = now
        start.authorized_until = now + timedelta(seconds=settings.MONITORING_AUTHORIZED_SECONDS)
        start.save(update_fields=["status", "confirmed_at", "authorized_until"])
        record_event(start.user, "monitoring.started", organization=device.organization, target=start)
    return True


def monitoring_status(user, now=None):
    """The state the employee's dashboard shows. "Monitoring active" only after the agent confirmed it."""
    device = _pc_for(user)
    if device is None:
        return STATUS_NOT_CONNECTED
    return monitoring_statuses([device], now)[device.pk]


def monitoring_statuses(devices, now=None):
    """The monitoring state of each device, as {device id: status}. Two queries for any number of devices.

    "Monitoring active" needs both: a start the agent confirmed, and the agent's latest heartbeat saying it
    is collecting. A click on Start Monitoring alone never shows it.
    """
    now = now or timezone.now()
    devices = list(devices)
    cutoff = now - timedelta(seconds=settings.DEVICE_ONLINE_SECONDS)
    pending = {}
    for start in MonitoringStart.objects.filter(device__in=devices, status=MonitoringStart.Status.PENDING):
        # An unexpired pending start wins over an expired one for the same device.
        if start.device_id not in pending or start.expires_at > now:
            pending[start.device_id] = start
    running = set(
        MonitoringStart.objects.filter(
            device__in=devices, status=MonitoringStart.Status.CONFIRMED, authorized_until__gt=now
        ).values_list("device_id", flat=True)
    )
    awaiting = set(
        MonitoringStart.objects.filter(
            device__in=devices, status=MonitoringStart.Status.AWAITING_CONSENT, expires_at__gt=now
        ).values_list("device_id", flat=True)
    )
    return {
        device.pk: _status(device, pending.get(device.pk), device.pk in running, now, cutoff, device.pk in awaiting)
        for device in devices
    }


def _status(device, pending, running, now, cutoff, awaiting=False):
    waiting = pending is not None and pending.expires_at > now
    if awaiting:
        # The employee must approve even when the PC is off; the approval is kept until it expires.
        return STATUS_AWAITING
    if device.last_seen_at is None or device.last_seen_at < cutoff:
        # Connection lost. A start that is still waiting is delivered when the PC is back within its time.
        return STATUS_REQUESTED if waiting else STATUS_OFFLINE
    if waiting:
        return STATUS_STARTING
    if running and device.monitoring_state == AGENT_COLLECTING:
        return STATUS_ACTIVE
    if running and device.monitoring_state in AGENT_PAUSED_STATES:
        return STATUS_PAUSED
    if pending or device.last_error_code:
        return STATUS_ERROR  # the start ran out before the agent confirmed it, or the agent reports an error
    return STATUS_READY
