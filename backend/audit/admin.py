from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only view. Nobody can add, change or delete audit records here."""

    list_display = ("created_at", "actor_email", "action", "organization", "target_type", "target_id")
    list_filter = ("action",)
    search_fields = ("actor_email", "action")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
