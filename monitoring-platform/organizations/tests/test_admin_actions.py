"""Super Admin actions on Admin accounts: edit, activate, deactivate and delete, with permission checks."""
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from accounts.tests.factories import make_org, make_user
from audit.models import AuditLog
from organizations.models import Organization


class AdminActionsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.other_org = make_org("Org B")
        cls.super_admin = User.objects.create_user(
            email="super@platform.test", password="x-test-pass-123", full_name="Super", role=Role.SUPER_ADMIN
        )
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org, full_name="Ada Admin")
        cls.other_admin = make_user("admin@b.test", Role.ADMIN, cls.other_org)
        cls.employee = make_user("emp@a.test", Role.EMPLOYEE, cls.org)

    def login(self, user):
        self.client.force_login(user)

    # --- edit -----------------------------------------------------------------------------------

    def test_super_admin_sees_the_edit_form_pre_filled(self):
        self.login(self.super_admin)
        response = self.client.get(reverse("organization_admin_edit", args=[self.admin.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="admin@a.test"')
        self.assertContains(response, 'value="Ada Admin"')

    def test_super_admin_can_edit_name_and_email(self):
        self.login(self.super_admin)
        response = self.client.post(
            reverse("organization_admin_edit", args=[self.admin.pk]),
            {"email": "ada@a.test", "full_name": "Ada Lovelace"},
        )
        self.assertRedirects(response, reverse("admin_list"))
        self.admin.refresh_from_db()
        self.assertEqual((self.admin.email, self.admin.full_name), ("ada@a.test", "Ada Lovelace"))
        self.assertTrue(AuditLog.objects.filter(action="admin.updated", target_id=str(self.admin.pk)).exists())

    def test_edit_keeps_the_same_email_without_a_duplicate_error(self):
        self.login(self.super_admin)
        response = self.client.post(
            reverse("organization_admin_edit", args=[self.admin.pk]),
            {"email": "admin@a.test", "full_name": "Ada Renamed"},
        )
        self.assertRedirects(response, reverse("admin_list"))

    def test_edit_refuses_an_email_another_account_in_the_organization_uses(self):
        self.login(self.super_admin)
        response = self.client.post(
            reverse("organization_admin_edit", args=[self.admin.pk]),
            {"email": "emp@a.test", "full_name": "Ada"},
        )
        self.assertEqual(response.status_code, 200)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.email, "admin@a.test")

    # --- activate and deactivate ------------------------------------------------------------------

    def test_super_admin_can_deactivate_and_reactivate(self):
        self.login(self.super_admin)
        self.client.post(reverse("organization_admin_set_active", args=[self.admin.pk]), {"active": "0"})
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_active)
        self.client.post(reverse("organization_admin_set_active", args=[self.admin.pk]), {"active": "1"})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    # --- delete -------------------------------------------------------------------------------------

    def test_super_admin_deletes_the_admin_and_keeps_organization_and_employees(self):
        self.login(self.super_admin)
        response = self.client.post(reverse("organization_admin_delete", args=[self.admin.pk]))
        self.assertRedirects(response, reverse("admin_list"))
        self.assertFalse(User.objects.filter(pk=self.admin.pk).exists())
        self.assertTrue(Organization.objects.filter(pk=self.org.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.employee.pk, organization=self.org).exists())
        self.assertTrue(AuditLog.objects.filter(action="admin.deleted", target_id=str(self.admin.pk)).exists())

    def test_delete_needs_a_post(self):
        self.login(self.super_admin)
        response = self.client.get(reverse("organization_admin_delete", args=[self.admin.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_an_admin_cannot_delete_an_admin(self):
        self.login(self.admin)
        self.client.post(reverse("organization_admin_delete", args=[self.other_admin.pk]))
        self.assertTrue(User.objects.filter(pk=self.other_admin.pk).exists())

    def test_an_admin_cannot_edit_an_admin(self):
        self.login(self.admin)
        response = self.client.post(
            reverse("organization_admin_edit", args=[self.other_admin.pk]),
            {"email": "hacked@b.test", "full_name": "Hacked"},
        )
        self.assertNotEqual(response.status_code, 200)
        self.other_admin.refresh_from_db()
        self.assertEqual(self.other_admin.email, "admin@b.test")

    def test_an_employee_cannot_deactivate_an_admin(self):
        self.login(self.employee)
        self.client.post(reverse("organization_admin_set_active", args=[self.admin.pk]), {"active": "0"})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_super_admin_cannot_reach_another_role_through_these_actions(self):
        # The edit and delete actions only work on Admin accounts, never on employees.
        self.login(self.super_admin)
        response = self.client.post(reverse("organization_admin_delete", args=[self.employee.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(User.objects.filter(pk=self.employee.pk).exists())


class DeleteSafetyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org C")
        cls.super_admin = User.objects.create_user(
            email="super2@platform.test", password="x-test-pass-123", full_name="Super", role=Role.SUPER_ADMIN
        )
        cls.admin = make_user("admin@c.test", Role.ADMIN, cls.org)

    def test_a_delete_is_refused_when_other_records_would_be_removed(self):
        from unittest import mock

        from organizations import services

        class FakeCollector:
            """Reports that deleting this Admin would also remove an organization record."""

            def __init__(self, using):
                self.data = {}

            def collect(self, objs):
                self.data = {User: [objs[0]], Organization: [organization]}

        organization = self.admin.organization
        with mock.patch.object(services, "Collector", FakeCollector):
            with self.assertRaises(ValueError):
                services.delete_organization_admin(self.super_admin, self.admin)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())
        self.assertTrue(self.admin.memberships.exists())
