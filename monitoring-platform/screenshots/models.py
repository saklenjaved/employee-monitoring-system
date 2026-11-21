import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q


class BlurLevel(models.TextChoices):
    NONE = "none", "No blur"
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class ScreenshotPolicy(models.Model):
    """Screenshot settings of one organization (PRD FR-03).

    There are no defaults for the interval, retention and blur: an Admin must choose them. Until all
    three are chosen, screenshots cannot be switched on.
    """

    organization = models.OneToOneField(
        "organizations.Organization", on_delete=models.CASCADE, related_name="screenshot_policy"
    )
    enabled = models.BooleanField(default=False)
    interval_seconds = models.PositiveIntegerField(
        null=True, blank=True, validators=[MinValueValidator(60), MaxValueValidator(3600)]
    )
    retention_days = models.PositiveIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(3650)]
    )
    blur_level = models.CharField(max_length=10, choices=BlurLevel.choices, blank=True, default="")
    # Who may look at screenshots besides Admins. Employees never can. Download is off by default.
    managers_can_view = models.BooleanField(default=True)
    managers_can_download = models.BooleanField(default=False)
    # Goes up on every change, so devices notice a changed policy.
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_configured(self):
        return self.interval_seconds is not None and self.retention_days is not None and bool(self.blur_level)

    @property
    def is_active(self):
        """True when screenshots may be captured for the organization."""
        return self.enabled and self.is_configured


class ScreenshotExclusion(models.Model):
    """Stops screenshots for one team or one employee. Exactly one of the two is set."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="+")
    team = models.ForeignKey("workforce.Team", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(team__isnull=False, user__isnull=True) | Q(team__isnull=True, user__isnull=False)),
                name="exclusion_is_a_team_or_a_user",
            ),
            models.UniqueConstraint(fields=["team"], condition=Q(team__isnull=False), name="unique_team_exclusion"),
            models.UniqueConstraint(
                fields=["organization", "user"], condition=Q(user__isnull=False), name="unique_user_exclusion_per_org"
            ),
        ]


class ScreenshotStatus(models.TextChoices):
    AUTHORIZED = "authorized", "Waiting for upload"
    UPLOADED = "uploaded", "Uploaded"
    FAILED = "failed", "Failed"


class Screenshot(models.Model):
    """Metadata of one screenshot. The image itself is in the private storage bucket."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    device = models.ForeignKey("devices.Device", on_delete=models.PROTECT, related_name="screenshots")
    # Chosen by the agent. It names the screenshot in the agent API, and is unique per device.
    screenshot_id = models.UUIDField()
    # Chosen by the server, random, and never taken from the agent.
    object_key = models.CharField(max_length=200, unique=True)
    captured_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=ScreenshotStatus.choices, default=ScreenshotStatus.AUTHORIZED)
    error_code = models.CharField(max_length=64, blank=True)
    content_type = models.CharField(max_length=20, default="image/png")
    byte_size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    redaction_applied = models.BooleanField(null=True, blank=True)

    class Meta:
        ordering = ["-captured_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["device", "screenshot_id"], name="unique_screenshot_per_device"),
        ]
        indexes = [
            models.Index(fields=["organization", "captured_at"]),
            models.Index(fields=["organization", "user", "captured_at"]),
            models.Index(fields=["status", "created_at"]),
        ]

    @staticmethod
    def new_object_key(organization_id, captured_at):
        """A random key. It holds no personal data, only the organization and the date."""
        return f"org-{organization_id}/{captured_at:%Y/%m/%d}/{uuid.uuid4().hex}.png"
