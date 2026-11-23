from django.conf import settings
from django.db import models


class AppendOnlyError(Exception):
    """Raised when code tries to change or delete an audit record outside the allowed path."""


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnlyError("Audit records cannot be updated.")

    def delete(self):
        raise AppendOnlyError("Audit records cannot be deleted.")


class AuditLogQuerySet(AppendOnlyQuerySet):
    def delete_by_service(self):
        """The one way to remove audit entries. Only audit.services.delete_entries calls it.

        That function checks that the actor is the Super Admin and writes the permanent AuditDeletion record
        in the same transaction. Everything else still gets AppendOnlyError from delete().
        """
        return models.QuerySet.delete(self)


class AuditLog(models.Model):
    """One record of an important action. Records are added and never changed (PRD FR-21).

    Only the Super Admin can delete them, through audit.services.delete_entries.
    """

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
        raise AppendOnlyError("Audit records can only be deleted by the Super Admin from the Audit logs page.")

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"


class AuditDeletion(models.Model):
    """A permanent record that a Super Admin deleted audit entries: who, when, how many and how.

    It is added in the same transaction as the deletion. Nobody can change or delete it.
    """

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    # A copy of the actor's email, so the record stays readable if the account is removed.
    actor_email = models.EmailField(blank=True)
    entry_count = models.PositiveIntegerField()
    # "single" (one entry), "selected" (ticked entries) or "matching" (every entry matching the filters).
    method = models.CharField(max_length=20)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnlyError("Deletion records cannot be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("Deletion records cannot be deleted.")

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} deleted {self.entry_count} ({self.method})"
