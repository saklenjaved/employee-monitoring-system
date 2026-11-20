from django.test import TestCase

from accounts.models import Role, User
from audit.models import AppendOnlyError, AuditLog
from audit.services import record_event
from organizations.models import Organization


class AuditLogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Org")
        cls.actor = User.objects.create_superuser(
            email="super@platform.test", password="a-long-test-password-1", full_name="Super"
        )

    def test_record_event_stores_actor_action_and_target(self):
        entry = record_event(self.actor, "organization.created", organization=self.org, target=self.org)
        self.assertEqual(entry.actor, self.actor)
        self.assertEqual(entry.actor_email, "super@platform.test")
        self.assertEqual(entry.action, "organization.created")
        self.assertEqual(entry.organization, self.org)
        self.assertEqual(entry.target_type, "organizations.Organization")
        self.assertEqual(entry.target_id, str(self.org.pk))

    def test_record_can_be_added_without_target(self):
        entry = record_event(self.actor, "platform.setting_changed")
        self.assertEqual(entry.target_type, "")

    def test_saved_record_cannot_be_changed(self):
        entry = record_event(self.actor, "organization.created")
        entry.action = "something.else"
        with self.assertRaises(AppendOnlyError):
            entry.save()

    def test_record_cannot_be_deleted(self):
        entry = record_event(self.actor, "organization.created")
        with self.assertRaises(AppendOnlyError):
            entry.delete()
        with self.assertRaises(AppendOnlyError):
            AuditLog.objects.all().delete()
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_bulk_update_is_blocked(self):
        record_event(self.actor, "organization.created")
        with self.assertRaises(AppendOnlyError):
            AuditLog.objects.all().update(action="x")

    def test_record_survives_removal_of_actor_account(self):
        admin = User.objects.create_user(
            email="admin@org.test", password="a-long-test-password-1", full_name="A", role=Role.ADMIN, organization=self.org
        )
        entry = record_event(admin, "user.admin_created")
        admin.delete()
        entry.refresh_from_db()
        self.assertIsNone(entry.actor)
        self.assertEqual(entry.actor_email, "admin@org.test")
