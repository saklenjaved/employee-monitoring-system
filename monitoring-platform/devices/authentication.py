from django.utils import timezone
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication

from accounts.models import MembershipStatus

from .models import Device
from .services import hash_secret


class DeviceAuthentication(BaseAuthentication):
    """Identify the agent by its device credential (Authorization: Bearer <credential>).

    On success `request.auth` is the Device and `request.user` is the employee who owns it.
    The organization comes from the device record, never from the request body.
    """

    def authenticate_header(self, request):
        return "Bearer"

    def authenticate(self, request):
        header = request.headers.get("Authorization", "")
        scheme, _, credential = header.partition(" ")
        if scheme.lower() != "bearer" or not credential.strip():
            raise exceptions.AuthenticationFailed("device credential required", code="missing_credential")

        device = (
            Device.objects.select_related("user", "organization", "membership")
            .filter(credential_hash=hash_secret(credential.strip()))
            .first()
        )
        if device is None:
            raise exceptions.AuthenticationFailed("credential not recognised; re-register", code="invalid_credential")
        if not device.is_active:
            raise exceptions.AuthenticationFailed("device was revoked; re-register", code="device_revoked")
        # The person must still be an active member of the device's organization. A removed or
        # transferred person's devices stop working even if a revoke was missed.
        if device.membership is None or device.membership.status != MembershipStatus.ACTIVE:
            raise exceptions.AuthenticationFailed("device was revoked; re-register", code="device_revoked")
        if device.credential_expires_at <= timezone.now():
            raise exceptions.AuthenticationFailed("credential expired; re-register", code="credential_expired")
        if not device.user.is_active or not device.organization.is_active:
            raise exceptions.PermissionDenied("account is disabled", code="account_disabled")
        return device.user, device
