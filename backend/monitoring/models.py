import datetime

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


def default_days():
    return [0, 1, 2, 3, 4]  # Monday to Friday. Monday is 0, as in the agent API.


class MonitoringPolicy(models.Model):
    """Monitoring hours and activity settings for one organization (PRD 4.3).

    Screenshot settings are added with the screenshot module.
    """

    organization = models.OneToOneField(
        "organizations.Organization", on_delete=models.CASCADE, related_name="monitoring_policy"
    )
    enabled = models.BooleanField(default=True)
    days = models.JSONField(default=default_days)
    start_time = models.TimeField(default=datetime.time(9, 0))
    end_time = models.TimeField(default=datetime.time(18, 0))
    sample_interval_seconds = models.PositiveIntegerField(
        default=30, validators=[MinValueValidator(5), MaxValueValidator(300)]
    )
    idle_threshold_seconds = models.PositiveIntegerField(
        default=300, validators=[MinValueValidator(60), MaxValueValidator(3600)]
    )
    # How often the agent uploads queued events. Shorter means fresher data, more requests.
    sync_interval_seconds = models.PositiveIntegerField(
        default=60, validators=[MinValueValidator(30), MaxValueValidator(900)]
    )
    # The organization's time zone (an IANA name such as "Asia/Kolkata"). Daily totals are
    # counted per calendar day in this zone. Default "UTC" (to confirm).
    timezone = models.CharField(max_length=64, default="UTC")
    # Goes up by one on every change, so a device can tell its policy is out of date.
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Policy for {self.organization} (v{self.version})"


class EventType(models.TextChoices):
    APP_FOCUS = "app_focus", "Application focus"
    ACTIVITY_SAMPLE = "activity_sample", "Activity sample"
    WEBSITE_VISIT = "website_visit", "Website visit"
    SESSION_STATE = "session_state", "Session state"


class MonitoringEvent(models.Model):
    """One event reported by a Windows agent, stored as received after validation.

    Organization and employee come from the device record, never from the agent.
    Summaries for the dashboards are built from these rows in a later phase.
    """

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    device = models.ForeignKey("devices.Device", on_delete=models.PROTECT, related_name="events")
    # Made on the agent (UUID4). Unique per device, so a resent event is stored once.
    event_id = models.UUIDField()
    event_type = models.CharField(max_length=20, choices=EventType.choices)
    captured_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    payload = models.JSONField()

    class Meta:
        ordering = ["captured_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["device", "event_id"], name="unique_event_per_device"),
        ]
        indexes = [
            models.Index(fields=["organization", "user", "captured_at"]),
            models.Index(fields=["device", "captured_at"]),
            models.Index(fields=["received_at"]),  # build_summaries looks up recently received events
        ]
