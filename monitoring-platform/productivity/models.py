from django.db import models
from django.db.models import Q


class ItemType(models.TextChoices):
    APP = "app", "Application"
    WEBSITE = "website", "Website"


class Category(models.TextChoices):
    PRODUCTIVE = "productive", "Productive"
    UNPRODUCTIVE = "unproductive", "Unproductive"
    NEUTRAL = "neutral", "Neutral"


# Shown for usage that no rule matches. It is not a rule category: Admins review these items.
UNCLASSIFIED = "unclassified"


class ClassificationRule(models.Model):
    """Says whether an application or website counts as productive, unproductive or neutral.

    A rule without a team applies to the whole organization. A team rule applies only to
    employees in that team and wins over an organization-wide rule (PRD FR-08).
    """

    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="+")
    item_type = models.CharField(max_length=10, choices=ItemType.choices)
    # An application's executable name (chrome.exe) or a website domain (example.com), lower case.
    # A domain also matches its subdomains (mail.example.com).
    pattern = models.CharField(max_length=260)
    category = models.CharField(max_length=15, choices=Category.choices)
    team = models.ForeignKey("workforce.Team", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["item_type", "pattern", "id"]
        constraints = [
            # One rule per item and scope. Two constraints, because NULL values never clash in a database.
            models.UniqueConstraint(
                fields=["organization", "item_type", "pattern"],
                condition=Q(team__isnull=True),
                name="unique_org_wide_rule",
            ),
            models.UniqueConstraint(
                fields=["organization", "item_type", "pattern", "team"],
                condition=Q(team__isnull=False),
                name="unique_team_rule",
            ),
        ]

    def __str__(self):
        return f"{self.item_type} {self.pattern} -> {self.category}"
