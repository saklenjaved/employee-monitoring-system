"""Device registration and status.

Ownership always comes from the registration code and the device record on the
server. Anything the agent sends about organizations or employees is never used.
"""
import hashlib
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from accounts.services import active_membership
from audit.services import record_event
from monitoring.services import policy_for_agent
from screenshots.services import agent_screenshot_policy, version_offset

from .models import Device, MonitoringStart, PairingRequest, RegistrationCode, SetupWindow
from .start import AGENT_ACCEPTING_STATES, confirm_start

logger = logging.getLogger(__name__)

# No 0, O, 1, I or L, so a code is easy to read out and type.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
SETUP_EMAIL_SUBJECT = "Set up monitoring on your computer"


class RegistrationError(Exception):
    """The registration code is not valid. The reason is deliberately not given."""


def hash_secret(value):
    """Hash of a code or credential. These values are random and long, so a plain SHA-256 is enough."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_code(code):
    return code.strip().upper()


def issue_registration_code(actor, user):
    """Create a single-use code for `user`. Returns the code text, which is not stored."""
    code = "-".join("".join(secrets.choice(CODE_ALPHABET) for _ in range(4)) for _ in range(3))
    now = timezone.now()
    with transaction.atomic():
        # A new code replaces the employee's earlier unused codes, so only the latest one works.
        RegistrationCode.objects.filter(user=user, used_at__isnull=True, expires_at__gt=now).update(expires_at=now)
        record = RegistrationCode.objects.create(
            organization=user.organization,
            user=user,
            code_hash=hash_secret(code),
            created_by=actor,
            expires_at=now + timedelta(days=settings.REGISTRATION_CODE_DAYS),
        )
        record_event(actor, "registration_code.created", organization=user.organization, target=record)
    return code, record


def register_device(code, device_name, os_version, agent_version, architecture=""):
    """Turn a valid registration code into a device and a credential.

    Returns (device, credential). The credential is returned once and only its hash is kept.
    """
    now = timezone.now()
    with transaction.atomic():
        record = (
            RegistrationCode.objects.select_for_update()
            .select_related("user", "organization")
            .filter(code_hash=hash_secret(_normalize_code(code)), used_at__isnull=True, expires_at__gt=now)
            .first()
        )
        if record is None or not record.user.is_active or not record.organization.is_active:
            raise RegistrationError()
        # The code only works while the person is still an active member of the code's organization.
        membership = active_membership(record.user, record.organization)
        if membership is None:
            raise RegistrationError()
        credential = secrets.token_urlsafe(32)
        device = Device.objects.create(
            organization=record.organization,
            user=record.user,
            membership=membership,
            name=device_name,
            os_version=os_version,
            agent_version=agent_version,
            architecture=architecture,
            credential_hash=hash_secret(credential),
            credential_expires_at=now + timedelta(days=settings.AGENT_CREDENTIAL_DAYS),
        )
        record.used_at = now
        record.used_by = device
        record.save(update_fields=["used_at", "used_by"])
        record_event(record.user, "device.registered", organization=record.organization, target=device)
    return device, credential


def revoke_device(actor, device):
    """Stop a device from using its credential and reset the employee's whole setup.

    The reset runs every time, even when the device was already revoked, so a repeat revoke starts the setup over.
    """
    with transaction.atomic():
        if device.is_active:
            device.is_active = False
            device.save(update_fields=["is_active"])
            record_event(actor, "device.revoked", organization=device.organization, target=device)
        reset_setup(device.user)


def revoke_devices_for(actor, organization, user):
    """Revoke every working device of `user` in `organization`, reset their setup and stop their unused registration codes.

    Used when the person leaves the organization, and when an Admin deactivates or reactivates them. The devices and
    everything they sent stay as history. Returns how many devices were revoked.
    """
    now = timezone.now()
    devices = list(Device.objects.filter(organization=organization, user=user, is_active=True))
    with transaction.atomic():
        for device in devices:
            revoke_device(actor, device)
        reset_setup(user)
        RegistrationCode.objects.filter(
            organization=organization, user=user, used_at__isnull=True, expires_at__gt=now
        ).update(expires_at=now)
    return len(devices)


def reset_setup(user):
    """Send the employee back to the start of the whole setup: download, install, password, Admin start, approval.

    The old download's token stops working, so the old agent cannot ask to connect again. The setup windows close,
    every approval, start or running monitoring for this employee ends, and pending pairing requests expire.
    """
    now = timezone.now()
    SetupWindow.objects.filter(user=user).update(install_token_hash="")
    SetupWindow.objects.filter(user=user, closed_at__isnull=True).update(closed_at=now)
    MonitoringStart.objects.filter(
        user=user,
        status__in=[MonitoringStart.Status.AWAITING_CONSENT, MonitoringStart.Status.PENDING, MonitoringStart.Status.CONFIRMED],
    ).update(status=MonitoringStart.Status.EXPIRED, expires_at=now)
    PairingRequest.objects.filter(
        organization=user.organization, status=PairingRequest.Status.PENDING
    ).update(status=PairingRequest.Status.EXPIRED, expires_at=now)


def record_heartbeat(device, data):
    """Store the status the agent reported. `data` is already validated."""
    device.last_seen_at = timezone.now()
    device.agent_version = data["agent_version"]
    device.session_type = data["session_type"]
    device.queue_depth = data["queue_depth"]
    # The agent leaves out last_error_code when there is no error, so a missing value clears it.
    device.last_error_code = data.get("last_error_code") or ""
    # These two are optional. A heartbeat that leaves them out keeps what is already stored.
    if data.get("last_successful_sync_at") is not None:
        device.last_successful_sync_at = data["last_successful_sync_at"]
    if data.get("monitoring_state"):
        device.monitoring_state = data["monitoring_state"]
    # The focus is current, not history. A heartbeat without it clears it, so nothing stale is shown.
    device.focus_app = data.get("focus_app") or ""
    device.focus_title = data.get("focus_title") or ""
    device.focus_updated_at = device.last_seen_at if device.focus_app else None
    device.save(
        update_fields=[
            "last_seen_at",
            "agent_version",
            "session_type",
            "queue_depth",
            "last_successful_sync_at",
            "last_error_code",
            "monitoring_state",
            "focus_app",
            "focus_title",
            "focus_updated_at",
        ]
    )
    # The agent confirms a start once it follows it: collecting, or paused by monitoring hours or a remote session.
    # Without this, a start sent outside monitoring hours ran out and showed as an error. The dashboards still
    # show "Monitoring active" only while the agent reports "active".
    if data.get("monitoring_state") in AGENT_ACCEPTING_STATES and data.get("monitoring_start_id"):
        confirm_start(device, data["monitoring_start_id"])


def agent_policy(device):
    """The full policy for a device: monitoring hours and activity settings plus the screenshot part.

    The version goes up when either part changes, and also when the device's exclusions change.
    """
    policy = policy_for_agent(device.organization)
    policy["screenshots"] = agent_screenshot_policy(device)
    policy["policy_version"] = str(int(policy["policy_version"]) + version_offset(device.organization))
    return policy


def mark_policy_served(device, policy_version):
    device.policy_version_served = policy_version
    device.save(update_fields=["policy_version_served"])


# --- Setup checklist for the Admin --------------------------------------------------------


def setup_steps(person, now=None):
    """How far one employee's monitoring setup has got, as (label, done) pairs, in the order of the work."""
    now = now or timezone.now()
    devices = Device.objects.filter(user=person, is_active=True)
    cutoff = now - timedelta(seconds=settings.DEVICE_ONLINE_SECONDS)
    minutes = settings.DEVICE_ONLINE_SECONDS // 60
    return [
        ("Sign-in link emailed and the approval window opened", SetupWindow.objects.filter(user=person).exists()),
        ("The employee approved the PC on their dashboard", devices.exists()),
        ("The agent received the monitoring rules", devices.exclude(policy_version_served="").exists()),
        (f"The agent is connected (seen in the last {minutes} minutes)", devices.filter(last_seen_at__gte=cutoff).exists()),
    ]


def next_setup_step(steps):
    """The label of the first step not done yet, or None when every step is done."""
    return next((label for label, done in steps if not done), None)


def connection_state(user, now=None):
    """The employee's PC, as "connected", "waiting" (registered, agent not seen lately) or "not_connected"."""
    now = now or timezone.now()
    devices = Device.objects.filter(user=user, is_active=True)
    cutoff = now - timedelta(seconds=settings.DEVICE_ONLINE_SECONDS)
    if devices.filter(last_seen_at__gte=cutoff).exists():
        return "connected"
    if devices.exists():
        return "waiting"
    return "not_connected"


def sync_state(device, now=None):
    """"offline" (not seen lately), "uploading" (queued items still to send), or "up_to_date"."""
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=settings.DEVICE_ONLINE_SECONDS)
    if device.last_seen_at is None or device.last_seen_at < cutoff:
        return "offline"
    if device.queue_depth:
        return "uploading"
    return "up_to_date"


def sync_status(user, now=None):
    """What the employee sees about their PC's agent, from its latest heartbeat.

    States: "not_connected", "offline", "uploading", "up_to_date" (see sync_state).
    """
    device = Device.objects.filter(user=user, is_active=True).order_by("-last_seen_at").first()
    if device is None:
        return {"state": "not_connected"}
    state = sync_state(device, now)
    return {
        "state": state,
        # What the PC is focused on right now. Hidden while it is offline, because it would not be current.
        "focus_app": device.focus_app if state != "offline" else "",
        "focus_title": device.focus_title if state != "offline" else "",
        "queue_depth": device.queue_depth,
        "last_sync": device.last_successful_sync_at,
        "last_seen": device.last_seen_at,
    }


def send_setup_invitation(request, person):
    """Email the employee a sign-in link to set up monitoring. The email holds no code. Returns True if sent."""
    context = {
        "full_name": person.full_name,
        "organization_name": person.organization.name,
        "login_url": request.build_absolute_uri(reverse("login")),
    }
    body = render_to_string("devices/setup_invitation.txt", context)
    try:
        send_mail(SETUP_EMAIL_SUBJECT, body, settings.DEFAULT_FROM_EMAIL, [person.email])
    except Exception as error:  # A mail problem must not become an error page. The Admin can try again.
        # Log the user ID and the error type only. The address is never logged.
        logger.warning("monitoring setup email failed for user %s (%s)", person.pk, type(error).__name__)
        return False
    return True
