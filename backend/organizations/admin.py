from django.contrib import admin

from audit.services import record_event

from .models import Organization


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name",)

    def has_delete_permission(self, request, obj=None):
        return False  # Organizations are deactivated, never deleted.

    def save_model(self, request, obj, form, change):
        """Changes made in the admin site are audited like every other change."""
        super().save_model(request, obj, form, change)
        if change:
            record_event(
                request.user,
                "organization.changed_in_admin",
                organization=obj,
                target=obj,
                detail=("fields: " + ", ".join(sorted(form.changed_data)))[:300],
            )
        else:
            record_event(request.user, "organization.created", organization=obj, target=obj)
