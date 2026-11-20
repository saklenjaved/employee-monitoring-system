from django.db import migrations
from django.utils import timezone


def copy_users_to_memberships(apps, schema_editor):
    """Give every existing organization user one active membership in their organization.

    A deactivated user stays deactivated through User.is_active, which an Admin can turn back on.
    Deactivation is not removal, so it does not end the membership. Nothing is lost.
    """
    User = apps.get_model("accounts", "User")
    Membership = apps.get_model("accounts", "Membership")
    now = timezone.now()

    for user in User.objects.exclude(role="super_admin").exclude(organization__isnull=True):
        Membership.objects.create(
            user=user,
            organization_id=user.organization_id,
            role=user.role,
            status="active",
            activated_at=now,
            created_by=None,
        )


def remove_copied_memberships(apps, schema_editor):
    Membership = apps.get_model("accounts", "Membership")
    Membership.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_membership_and_activation"),
    ]

    operations = [
        migrations.RunPython(copy_users_to_memberships, remove_copied_memberships),
    ]
