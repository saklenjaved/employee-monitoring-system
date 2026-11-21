"""Connecting a new PC without a code (docs/employee-pairing-plan.md).

Flow: an Admin opens a setup window for an employee. The PC asks to connect and gets a pairing request
with no account attached. While the window is open, the employee sees the waiting PC on their dashboard
and approves it with their password. Approval creates the device for that employee and queues the
monitoring. The agent then collects its credential once, using a secret token it kept.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from accounts.services import active_membership
from audit.services import record_event
from organizations.models import Organization

from .models import Device, MonitoringStart, PairingRequest, SetupWindow
from .services import hash_secret


class PairingError(Exception):
    """A pairing step that cannot be done. `code` is a short reason for the message shown to the user."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def open_setup_window(actor, employee, now=None):
    """Open a setup window for `employee`. Returns (window, None) or (None, "another_open")."""
    now = now or timezone.now()
    organization = employee.organization
    with transaction.atomic():
        SetupWindow.objects.filter(
            organization=organization, closed_at__isnull=True, expires_at__lte=now
        ).update(closed_at=now)
        # The employee's install token carries over, so an installed PC keeps its token across setup windows.
        # Only a new download replaces it.
        previous = SetupWindow.objects.filter(user=employee).order_by("-opened_at").first()
        try:
            with transaction.atomic():
                window = SetupWindow.objects.create(
                    organization=organization,
                    user=employee,
                    install_token_hash=previous.install_token_hash if previous else "",
                    opened_by=actor,
                    opened_at=now,
                    expires_at=now + timedelta(minutes=settings.SETUP_WINDOW_MINUTES),
                )
        except IntegrityError:
            return None, "another_open"
        record_event(actor, "setup_window.opened", organization=organization, target=window)
    return window, None


def ensure_setup_window(user, now=None):
    """Open an approval window for this employee's own download. Returns the window, or None.

    None means another employee's window is open right now. Only one window is open per organization,
    so a waiting PC is never shown to the wrong employee. The employee tries again in a few minutes.
    """
    now = now or timezone.now()
    organization = user.organization
    with transaction.atomic():
        SetupWindow.objects.filter(organization=organization, closed_at__isnull=True, expires_at__lte=now).update(
            closed_at=now
        )
        open_now = SetupWindow.objects.filter(organization=organization, closed_at__isnull=True).first()
        if open_now is not None:
            return open_now if open_now.user_id == user.pk else None
        previous = SetupWindow.objects.filter(user=user).order_by("-opened_at").first()
        return SetupWindow.objects.create(
            organization=organization,
            user=user,
            install_token_hash=previous.install_token_hash if previous else "",
            opened_by=user,
            opened_at=now,
            expires_at=now + timedelta(minutes=settings.SETUP_WINDOW_MINUTES),
        )


def open_window_for(user, now=None):
    now = now or timezone.now()
    return SetupWindow.objects.filter(user=user, closed_at__isnull=True, expires_at__gt=now).first()


def issue_enrollment_token(organization):
    """A new enrollment token for the organization's installer. Shown once; only its hash is kept."""
    token = secrets.token_urlsafe(24)
    organization.enrollment_token_hash = hash_secret(token)
    organization.save(update_fields=["enrollment_token_hash"])
    return token


def organization_for_token(token):
    """The active organization that owns this enrollment token, or None.

    Two kinds of token work: an organization's installer token, and the token in an employee's install
    package. That token keeps working after its setup window closes or a newer window opens, so an installed
    PC can ask to connect again. It stops only when the employee downloads a new installer. Approval still
    needs an open window and the employee's password.
    """
    if not token:
        return None
    token_hash = hash_secret(token)
    organization = Organization.objects.filter(enrollment_token_hash=token_hash, is_active=True).first()
    if organization is not None:
        return organization
    window = SetupWindow.objects.filter(
        install_token_hash=token_hash, organization__is_active=True, user__is_active=True
    ).select_related("organization", "user").order_by("-opened_at").first()
    if window is None:
        return None
    # Only the employee's current token counts. An older download's token stops with the next download.
    current = SetupWindow.objects.filter(user=window.user).order_by("-opened_at").first()
    return window.organization if current is not None and current.install_token_hash == token_hash else None


def issue_install_token(window):
    """A new install token for this setup window. It replaces the previous one, so only the latest download works."""
    token = secrets.token_urlsafe(24)
    window.install_token_hash = hash_secret(token)
    window.save(update_fields=["install_token_hash"])
    return token


def create_pairing_request(details, organization, now=None):
    """A PC asks to connect. Returns (request, poll_token). The token is shown once, to the agent.

    A PC that asks again while its earlier request is still waiting gets that same request back, with a new
    poll token. So each PC has at most one card on the dashboard, however often the agent retries.
    """
    now = now or timezone.now()
    token = secrets.token_urlsafe(32)
    with transaction.atomic():
        request = (
            PairingRequest.objects.select_for_update()
            .filter(
                organization=organization,
                device_name=details["device_name"],
                status=PairingRequest.Status.PENDING,
                expires_at__gt=now,
            )
            .order_by("-requested_at")
            .first()
        )
        if request is None:
            request = PairingRequest(organization=organization, device_name=details["device_name"])
        # The newest request wins. The old poll token stops working, and the agent that asked gets the new one.
        request.poll_token_hash = hash_secret(token)
        request.os_version = details["os_version"]
        request.agent_version = details["agent_version"]
        request.architecture = details.get("architecture", "")
        request.requested_at = now
        request.expires_at = now + timedelta(minutes=settings.PAIRING_REQUEST_MINUTES)
        request.save()
    return request, token


def waiting_pcs_for(user, now=None):
    """PCs the employee may approve right now: open window, and requests still waiting since it opened."""
    now = now or timezone.now()
    window = open_window_for(user, now)
    if window is None:
        return PairingRequest.objects.none()
    # A PC that asked a few minutes before the window opened is still shown, since its request is not yet expired.
    earliest = window.opened_at - timedelta(minutes=settings.PAIRING_REQUEST_MINUTES)
    return PairingRequest.objects.filter(
        organization=window.organization,
        status=PairingRequest.Status.PENDING,
        expires_at__gt=now,
        requested_at__gte=earliest,
    )


def awaiting_first_contact(user, now=None):
    """The PC this employee approved a moment ago, whose agent has not checked in since. None if there is none.

    The agent collects its credential within seconds of the approval. Until it does, the dashboard says
    "Connecting" instead of "PC offline". After PAIRING_REQUEST_MINUTES it counts as offline again.
    """
    now = now or timezone.now()
    return (
        PairingRequest.objects.filter(
            device__user=user,
            device__is_active=True,
            status=PairingRequest.Status.APPROVED,
            approved_at__gte=now - timedelta(minutes=settings.PAIRING_REQUEST_MINUTES),
        )
        .filter(Q(device__last_seen_at__isnull=True) | Q(device__last_seen_at__lt=F("approved_at")))
        .select_related("device")
        .order_by("-approved_at")
        .first()
    )


def approve_request(user, request_id, now=None):
    """The employee approves one waiting PC. Creates or updates the device. It does not start monitoring."""
    now = now or timezone.now()
    with transaction.atomic():
        window = SetupWindow.objects.select_for_update().filter(
            user=user, closed_at__isnull=True, expires_at__gt=now
        ).first()
        if window is None:
            raise PairingError("no_window")
        request = PairingRequest.objects.select_for_update().filter(pk=request_id).first()
        if request is None or request not in waiting_pcs_for(user, now):
            raise PairingError("not_waiting")
        membership = active_membership(user, user.organization)
        if membership is None:
            raise PairingError("no_membership")
        # The same PC approved again reuses its device, so no duplicate Device rows appear.
        device = (
            Device.objects.select_for_update()
            .filter(organization=user.organization, user=user, name=request.device_name)
            .order_by("-registered_at")
            .first()
        )
        if device is None:
            device = Device(organization=user.organization, user=user, membership=membership, name=request.device_name)
        # Approving again also brings back a PC that was revoked, since the employee chose to approve it.
        device.is_active = True
        device.membership = membership
        device.os_version = request.os_version
        device.agent_version = request.agent_version
        device.architecture = request.architecture
        # The real credential is issued when the agent collects it. Until then, nothing valid exists.
        device.credential_hash = hash_secret(secrets.token_urlsafe(32))
        device.credential_expires_at = now
        device.save()
        request.status = PairingRequest.Status.APPROVED
        request.approved_by = user
        request.approved_at = now
        request.device = device
        request.save(update_fields=["status", "approved_by", "approved_at", "device"])
        window.closed_at = now
        window.save(update_fields=["closed_at"])
        record_event(user, "device.pairing_approved", organization=user.organization, target=device)
        # Approving the PC only registers it. Monitoring starts later: the Admin asks, then the employee confirms.
        # A start left pending from an earlier approval of this PC is replaced, so only one stays pending.
        MonitoringStart.objects.filter(device=device, status=MonitoringStart.Status.PENDING).update(
            status=MonitoringStart.Status.EXPIRED
        )
    return device


def renew_credential(device, now=None):
    """Issue a fresh credential for an already-approved device. Returns (credential, expires_at).

    No employee approval is needed: the agent proves itself with its current credential. The old
    credential stops working as soon as this runs.
    """
    now = now or timezone.now()
    credential = secrets.token_urlsafe(32)
    device.credential_hash = hash_secret(credential)
    device.credential_expires_at = now + timedelta(days=settings.AGENT_CREDENTIAL_DAYS)
    device.save(update_fields=["credential_hash", "credential_expires_at"])
    return credential, device.credential_expires_at


def _token_matches(request, token):
    return hmac.compare_digest(hash_secret(token), request.poll_token_hash)


def poll_pairing(request_id, token, now=None):
    """The agent asks how its request is going. Returns a status dict, or None if the request or token is wrong.

    The credential is issued on the first poll after approval and never again.
    """
    now = now or timezone.now()
    with transaction.atomic():
        request = PairingRequest.objects.select_for_update().filter(pk=request_id).first()
        if request is None or not _token_matches(request, token):
            return None
        if request.status == PairingRequest.Status.PENDING and request.expires_at <= now:
            request.status = PairingRequest.Status.EXPIRED
            request.save(update_fields=["status"])
        if request.status == PairingRequest.Status.PENDING:
            return {"status": "pending"}
        if request.status == PairingRequest.Status.EXPIRED:
            return {"status": "expired"}
        if request.credential_delivered_at is not None:
            return {"status": "delivered"}  # the credential was already handed over; it is never repeated
        device = request.device
        if not device.is_active:
            return {"status": "expired"}
        credential = secrets.token_urlsafe(32)
        device.credential_hash = hash_secret(credential)
        device.credential_expires_at = now + timedelta(days=settings.AGENT_CREDENTIAL_DAYS)
        device.save(update_fields=["credential_hash", "credential_expires_at"])
        request.credential_delivered_at = now
        request.save(update_fields=["credential_delivered_at"])
        return {
            "status": "approved",
            "device_id": str(device.id),
            "credential": credential,
            "expires_at": device.credential_expires_at,
        }
