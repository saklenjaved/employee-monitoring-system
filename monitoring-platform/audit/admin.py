from django.contrib import admin

from accounts.models import Role

from .models import AuditDeletion, AuditLog
from .services import delete_entries


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Nobody can add or change audit records here. Only the Super Admin can delete them, which is recorded."""

    list_display = ("created_at", "actor_email", "action", "organization", "target_type", "target_id")
    list_filter = ("action",)
    search_fields = ("actor_email", "action")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.role == Role.SUPER_ADMIN

    # Deleting goes through the audit service, so the permission check and the deletion record always apply.
    def delete_model(self, request, obj):
        delete_entries(request.user, AuditLog.objects.filter(pk=obj.pk), "single")

    def delete_queryset(self, request, queryset):
        delete_entries(request.user, queryset, "selected")


@admin.register(AuditDeletion)
class AuditDeletionAdmin(admin.ModelAdmin):
    """The permanent record of audit deletions. Read-only."""

    list_display = ("created_at", "actor_email", "entry_count", "method")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
