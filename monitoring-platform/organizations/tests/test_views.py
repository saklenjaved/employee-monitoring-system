from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from accounts.tests.factories import make_membership
from audit.models import AuditLog
from organizations.models import Organization

PASSWORD = "a-long-test-password-1"


class OrganizationManagementTests(TestCase):
    def setUp(self):
        cache.clear()  # the login page counts failed attempts in the cache

    @classmethod
    def setUpTestData(cls):
        cls.org_a = Organization.objects.create(name="Org A")
        cls.org_b = Organization.objects.create(name="Org B")
        cls.super_admin = User.objects.create_superuser(
            email="super@platform.test", password=PASSWORD, full_name="Super"
        )
        cls.admin_a = User.objects.create_user(
            email="admin@a.test", password=PASSWORD, full_name="Admin A", role=Role.ADMIN, organization=cls.org_a
        )
        make_membership(cls.admin_a)

    def test_super_admin_can_create_organization_and_it_is_audited(self):
        self.client.force_login(self.super_admin)
        response = self.client.post(reverse("organization_create"), {"name": "New Co"})
        self.assertRedirects(response, reverse("organization_list"))
        org = Organization.objects.get(name="New Co")
        entry = AuditLog.objects.get(action="organization.created")
        self.assertEqual(entry.actor, self.super_admin)
        self.assertEqual(entry.organization, org)

    def test_duplicate_organization_name_is_rejected(self):
        self.client.force_login(self.super_admin)
        response = self.client.post(reverse("organization_create"), {"name": "Org A"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Organization.objects.filter(name="Org A").count(), 1)

    def test_list_shows_all_organizations(self):
        self.client.force_login(self.super_admin)
        response = self.client.get(reverse("organization_list"))
        self.assertContains(response, "Org A")
        self.assertContains(response, "Org B")

    def test_super_admin_can_deactivate_and_activate_with_audit(self):
        self.client.force_login(self.super_admin)
        url = reverse("organization_set_active", args=[self.org_a.pk])
        self.client.post(url, {"active": "0"})
        self.org_a.refresh_from_db()
        self.assertFalse(self.org_a.is_active)
        self.assertTrue(AuditLog.objects.filter(action="organization.deactivated", organization=self.org_a).exists())

        self.client.post(url, {"active": "1"})
        self.org_a.refresh_from_db()
        self.assertTrue(self.org_a.is_active)
        self.assertTrue(AuditLog.objects.filter(action="organization.activated", organization=self.org_a).exists())

    def test_deactivate_by_get_is_not_allowed(self):
        self.client.force_login(self.super_admin)
        response = self.client.get(reverse("organization_set_active", args=[self.org_a.pk]))
        self.assertEqual(response.status_code, 405)

    def test_setting_same_state_writes_no_audit_entry(self):
        self.client.force_login(self.super_admin)
        self.client.post(reverse("organization_set_active", args=[self.org_a.pk]), {"active": "1"})
        self.assertFalse(AuditLog.objects.filter(action="organization.activated").exists())

    def test_super_admin_can_create_admin_account_and_email_is_sent(self):
        self.client.force_login(self.super_admin)
        url = reverse("organization_admin_create", args=[self.org_b.pk])
        response = self.client.post(url, {"email": "New.Admin@B.test", "full_name": "New Admin"})
        self.assertRedirects(response, reverse("organization_list"))

        admin = User.objects.get(email="new.admin@b.test")
        self.assertEqual(admin.role, Role.ADMIN)
        self.assertEqual(admin.organization, self.org_b)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["new.admin@b.test"])
        entry = AuditLog.objects.get(action="membership.invited")
        self.assertEqual(entry.organization, self.org_b)
        self.assertEqual(entry.target_id, str(admin.pk))

    def test_new_admin_cannot_log_in_with_a_guessed_password(self):
        self.client.force_login(self.super_admin)
        self.client.post(
            reverse("organization_admin_create", args=[self.org_b.pk]),
            {"email": "new.admin@b.test", "full_name": "New Admin"},
        )
        self.client.logout()
        response = self.client.post(reverse("login"), {"username": "new.admin@b.test", "password": ""})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_duplicate_admin_email_in_the_same_organization_is_rejected(self):
        self.client.force_login(self.super_admin)
        url = reverse("organization_admin_create", args=[self.org_a.pk])
        response = self.client.post(url, {"email": "ADMIN@a.test", "full_name": "Copy"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already exists in this organization")
        self.assertEqual(User.objects.filter(email="admin@a.test").count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_the_same_admin_email_is_allowed_in_another_organization(self):
        self.client.force_login(self.super_admin)
        url = reverse("organization_admin_create", args=[self.org_b.pk])
        response = self.client.post(url, {"email": "ADMIN@a.test", "full_name": "Admin B"})
        self.assertRedirects(response, reverse("organization_list"))
        accounts = User.objects.filter(email="admin@a.test").order_by("pk")
        self.assertEqual([account.organization for account in accounts], [self.org_a, self.org_b])
        self.assertEqual(len(mail.outbox), 1)

    def test_the_super_admin_email_cannot_be_given_to_an_organization_admin(self):
        self.client.force_login(self.super_admin)
        url = reverse("organization_admin_create", args=[self.org_b.pk])
        response = self.client.post(url, {"email": self.super_admin.email, "full_name": "Copy"})
        self.assertContains(response, "This email cannot be used.")
        self.assertEqual(User.objects.filter(email=self.super_admin.email).count(), 1)

    def test_admin_of_one_organization_cannot_manage_organizations(self):
        self.client.force_login(self.admin_a)
        for name, args in [
            ("organization_list", []),
            ("organization_create", []),
            ("organization_admin_create", [self.org_b.pk]),
        ]:
            with self.subTest(view=name):
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403)
        response = self.client.post(reverse("organization_set_active", args=[self.org_b.pk]), {"active": "0"})
        self.assertEqual(response.status_code, 403)
        self.org_b.refresh_from_db()
        self.assertTrue(self.org_b.is_active)

    def test_admin_cannot_create_admin_in_another_organization(self):
        self.client.force_login(self.admin_a)
        response = self.client.post(
            reverse("organization_admin_create", args=[self.org_b.pk]),
            {"email": "intruder@b.test", "full_name": "Intruder"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(email="intruder@b.test").exists())
