from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from audit.models import AuditLog
from devices.models import Device, RegistrationCode
from devices.services import hash_secret, register_device


class RegistrationCodePageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.manager_a = make_user("manager@a.test", Role.MANAGER, cls.org_a)
        cls.emp_a = make_user("e@a.test", Role.EMPLOYEE, cls.org_a)
        cls.emp_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b)

    def create_code(self, user):
        return self.client.post(reverse("registration_code_create", args=[user.pk]))

    def test_admin_creates_a_code_for_an_employee_and_it_is_shown_once(self):
        self.client.force_login(self.admin_a)
        response = self.create_code(self.emp_a)
        self.assertEqual(response.status_code, 200)
        record = RegistrationCode.objects.get()
        self.assertEqual((record.user, record.organization, record.created_by), (self.emp_a, self.org_a, self.admin_a))
        self.assertIsNone(record.used_at)
        # The page shows a code whose hash is the stored one. The code itself is not stored.
        code = response.context["code"]
        self.assertRegex(code, r"^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$")
        self.assertContains(response, code)
        self.assertEqual(record.code_hash, hash_secret(code))
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertTrue(AuditLog.objects.filter(action="registration_code.created", organization=self.org_a).exists())

    def test_code_cannot_be_created_for_another_organizations_employee(self):
        self.client.force_login(self.admin_a)
        self.assertEqual(self.create_code(self.emp_b).status_code, 404)
        self.assertFalse(RegistrationCode.objects.exists())

    def test_code_is_only_for_active_employees(self):
        self.client.force_login(self.admin_a)
        self.create_code(self.manager_a)
        self.emp_a.is_active = False
        self.emp_a.save()
        self.create_code(self.emp_a)
        self.assertFalse(RegistrationCode.objects.exists())

    def test_other_roles_cannot_create_codes(self):
        for user in (self.manager_a, self.emp_a):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                self.assertEqual(self.create_code(self.emp_a).status_code, 403)
        self.assertFalse(RegistrationCode.objects.exists())

    def test_get_is_not_allowed(self):
        self.client.force_login(self.admin_a)
        response = self.client.get(reverse("registration_code_create", args=[self.emp_a.pk]))
        self.assertEqual(response.status_code, 405)


class DeviceRevokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.admin_b = make_user("admin@b.test", Role.ADMIN, cls.org_b)
        cls.emp_a = make_user("e@a.test", Role.EMPLOYEE, cls.org_a)
        cls.emp_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b)

    def make_device(self, admin, employee):
        from devices.services import issue_registration_code

        code, _ = issue_registration_code(admin, employee)
        device, _ = register_device(code, "LAPTOP", "Windows 11", "0.1.0")
        return device

    def test_admin_revokes_a_device_of_own_organization(self):
        device = self.make_device(self.admin_a, self.emp_a)
        self.client.force_login(self.admin_a)
        response = self.client.post(reverse("device_revoke", args=[device.pk]))
        self.assertRedirects(response, reverse("person_detail", args=[self.emp_a.pk]))
        device.refresh_from_db()
        self.assertFalse(device.is_active)
        self.assertTrue(AuditLog.objects.filter(action="device.revoked", organization=self.org_a).exists())

    def test_admin_cannot_revoke_a_device_of_another_organization(self):
        device = self.make_device(self.admin_b, self.emp_b)
        self.client.force_login(self.admin_a)
        self.assertEqual(self.client.post(reverse("device_revoke", args=[device.pk])).status_code, 404)
        device.refresh_from_db()
        self.assertTrue(device.is_active)

    def test_person_page_lists_only_own_devices(self):
        own = self.make_device(self.admin_a, self.emp_a)
        self.make_device(self.admin_b, self.emp_b)
        self.client.force_login(self.admin_a)
        response = self.client.get(reverse("person_detail", args=[self.emp_a.pk]))
        self.assertEqual(list(response.context["devices"]), [own])
        self.assertEqual(Device.objects.count(), 2)
