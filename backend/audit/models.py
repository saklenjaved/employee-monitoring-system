from django.conf import settings
from django.db import models


class AppendOnlyError(Exception):
    """Raised when code tries to change or delete an audit record."""


class AuditLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnlyError("Audit records cannot be updated.")

    def delete(self):
        raise AppendOnlyError("Audit records cannot be deleted.")


class AuditLog(models.Model):
    """One record of an important action. Records are only ever added (PRD FR-21)."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    # A copy of the actor's email, so the record stays readable if the account is removed.
    actor_email = models.EmailField(blank=True)
    action = models.CharField(max_length=100)  # for example "organization.created"
    # The organization the action affected. Empty for platform-level actions.
    organization = models.ForeignKey(
        "organizations.Organization", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    target_type = models.CharField(max_length=100, blank=True)
    target_id = models.CharField(max_length=50, blank=True)
    # Short note. Never put tokens, signed URLs or personal details here.
    detail = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["organization", "created_at"])]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnlyError("Audit records cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("Audit records cannot be deleted.")

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"
