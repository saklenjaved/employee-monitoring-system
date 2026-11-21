from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from audit.services import record_event

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("email",)
    list_display = ("email", "full_name", "role", "organization", "is_active")
    list_filter = ("role", "organization", "is_active")
    search_fields = ("email", "full_name")

    def has_delete_permission(self, request, obj=None):
        return False  # Accounts are deactivated, never deleted.

    def save_model(self, request, obj, form, change):
        """Changes made in the admin site are audited like every other change.

        The admin wraps this in a transaction, so the record is saved with the change.
        """
        super().save_model(request, obj, form, change)
        if change:
            record_event(
                request.user,
                "user.changed_in_admin",
                organization=obj.organization,
                target=obj,
                detail=("fields: " + ", ".join(sorted(form.changed_data)))[:300],
            )
        else:
            record_event(request.user, "user.created_in_admin", organization=obj.organization, target=obj)

    def user_change_password(self, request, id, form_url=""):
        response = super().user_change_password(request, id, form_url)
        if request.method == "POST" and response.status_code == 302:
            user = self.get_object(request, id)
            record_event(request.user, "user.password_changed_in_admin", organization=user.organization, target=user)
        return response
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Profile", {"fields": ("full_name", "role", "organization")}),
        ("Status", {"fields": ("is_active", "is_staff", "is_superuser")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "full_name", "role", "organization", "password1", "password2"),
            },
        ),
    )
