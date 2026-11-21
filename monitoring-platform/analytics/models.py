from django.conf import settings
from django.db import models


class ItemUsageDaily(models.Model):
    """Seconds one employee spent in one application or on one website on one day.

    Built from the raw events by analytics.summaries. The category is NOT stored: it is
    worked out when the data is read, so a changed rule applies at once (PRD FR-08).
    """

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    date = models.DateField()  # calendar day in the organization's time zone
    item_type = models.CharField(max_length=10)  # "app" or "website"
    name = models.CharField(max_length=260)  # executable name or domain, lower case
    seconds = models.FloatField()

    class Meta:
        constraints = [
            # Per organization: after a transfer the old organization's days stay as they were.
            models.UniqueConstraint(
                fields=["organization", "user", "date", "item_type", "name"], name="unique_item_usage_per_org_day"
            ),
        ]
        indexes = [models.Index(fields=["organization", "date"])]


class ActivityDaily(models.Model):
    """Active and idle seconds of one employee on one day."""

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    date = models.DateField()
    active_seconds = models.FloatField(default=0)
    idle_seconds = models.FloatField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "user", "date"], name="unique_activity_per_org_day"),
        ]
        indexes = [models.Index(fields=["organization", "date"])]
