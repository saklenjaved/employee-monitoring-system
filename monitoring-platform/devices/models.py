import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Device(models.Model):
    """A registered Windows agent. The device record decides who owns its data."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="devices")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="devices")
    # The organization membership the device was registered under. A person may have several
    # devices per membership. When the membership ends, its devices stop working.
    membership = models.ForeignKey(
        "accounts.Membership", null=True, blank=True, on_delete=models.PROTECT, related_name="devices"
    )
    name = models.CharField(max_length=128)
    os_version = models.CharField(max_length=64)
    agent_version = models.CharField(max_length=32)
    architecture = models.CharField(max_length=16, blank=True)

    # Only a hash of the credential is stored. The credential itself is shown to the agent once.
    credential_hash = models.CharField(max_length=64, unique=True)
    credential_expires_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)  # False after an Admin revokes the device
    registered_at = models.DateTimeField(auto_now_add=True)

    # Status reported by the agent heartbeat.
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_successful_sync_at = models.DateTimeField(null=True, blank=True)
    session_type = models.CharField(max_length=16, blank=True)
    queue_depth = models.PositiveIntegerField(default=0)
    last_error_code = models.CharField(max_length=64, blank=True)
    monitoring_state = models.CharField(max_length=32, blank=True)
    # The policy version this device last downloaded.
    policy_version_served = models.CharField(max_length=20, blank=True)
    # The focused app and window from the latest heartbeat. Empty when the agent did not send them.
    focus_app = models.CharField(max_length=128, blank=True)
    focus_title = models.CharField(max_length=256, blank=True)
    focus_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-registered_at"]
        indexes = [models.Index(fields=["organization", "user"])]

    @property
    def is_online(self):
        if self.last_seen_at is None:
            return False
        return (timezone.now() - self.last_seen_at).total_seconds() < settings.DEVICE_ONLINE_SECONDS

    def __str__(self):
        return self.name


class RegistrationCode(models.Model):
    """A single-use code an Admin gives an employee to connect one device."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="registration_codes")
    # Only a hash is stored. The code is shown to the Admin once, when it is created.
    code_hash = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    used_by = models.ForeignKey(Device, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")


class MonitoringStart(models.Model):
    """One employee request to start monitoring on one PC. Created only by the server, after the PIN is checked."""

    class Status(models.TextChoices):
        AWAITING_CONSENT = "awaiting_consent", "Waiting for employee consent"
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="monitoring_starts")
    device = models.ForeignKey(Device, on_delete=models.PROTECT, related_name="monitoring_starts")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    requested_at = models.DateTimeField(auto_now_add=True)
    # A pending request that the agent has not confirmed by this time is given up.
    expires_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # Once confirmed, the agent may collect until this time. A later heartbeat renews nothing by itself.
    authorized_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]
        indexes = [models.Index(fields=["device", "status"])]
        constraints = [
            models.UniqueConstraint(
                fields=["device"], condition=models.Q(status="pending"), name="one_pending_start_per_device"
            )
        ]


class SetupWindow(models.Model):
    """A time-limited window in which one employee may approve a new PC. An Admin opens it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="setup_windows")
    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    opened_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)
    # The token in the employee's one-click install package. Only its hash is stored, and each download replaces it.
    install_token_hash = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-opened_at"]
        constraints = [
            # Only one setup window is open per organization at a time, so an approval prompt can never
            # appear for two employees at once.
            models.UniqueConstraint(
                fields=["organization"], condition=models.Q(closed_at__isnull=True), name="one_open_setup_window_per_org"
            )
        ]


class PairingRequest(models.Model):
    """A PC asking to connect. It belongs to no employee until an employee approves it."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Only a hash of the poll token is stored. The token is given to the agent once, when it asks.
    poll_token_hash = models.CharField(max_length=64)
    device_name = models.CharField(max_length=128)
    os_version = models.CharField(max_length=64)
    agent_version = models.CharField(max_length=32)
    architecture = models.CharField(max_length=16, blank=True)
    # Set from the enrollment token the PC sent. Only this organization's employees can see the request.
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="pairing_requests", null=True, blank=True
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    requested_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    device = models.ForeignKey(Device, null=True, blank=True, on_delete=models.SET_NULL, related_name="pairing_requests")
    # Set when the agent has collected its credential. The credential is handed over only once.
    credential_delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]
        indexes = [models.Index(fields=["status", "expires_at"])]
