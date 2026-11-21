from django.db import migrations


def link_devices(apps, schema_editor):
    """Link every existing device to the membership of its owner in the device's organization.

    The newest membership is used. A device whose owner has no membership in that
    organization stays unlinked, and the agent API refuses it until it is registered again.
    """
    Device = apps.get_model("devices", "Device")
    Membership = apps.get_model("accounts", "Membership")
    for device in Device.objects.filter(membership__isnull=True):
        membership = (
            Membership.objects.filter(user_id=device.user_id, organization_id=device.organization_id)
            .order_by("-created_at", "-pk")
            .first()
        )
        if membership is not None:
            device.membership = membership
            device.save(update_fields=["membership"])


def unlink_devices(apps, schema_editor):
    Device = apps.get_model("devices", "Device")
    Device.objects.update(membership=None)


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0002_device_membership"),
        ("accounts", "0003_copy_users_to_memberships"),
    ]

    operations = [
        migrations.RunPython(link_devices, unlink_devices),
    ]
