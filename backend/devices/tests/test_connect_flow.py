"""The setup flow: the Admin starts it, the employee gets a sign-in link, and the employee gets a code
on their own dashboard for their own PC."""
import re
from unittest import mock

from django.core import mail
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from audit.models import AuditLog
from devices.models import RegistrationCode
from devices.services import SETUP_EMAIL_SUBJECT

from .test_setup import make_device

CODE_PATTERN = re.compile(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}")


class StartSetupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.manager_a = make_user("m@a.test", Role.MANAGER, cls.org_a)
        cls.emp_a = make_user("e@a.test", Role.EMPLOYEE, cls.org_a, "Employee A")
        cls.emp_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b, "Employee B")

    def start(self, person):
        return self.client.post(reverse("monitoring_setup_start", args=[person.pk]))

    def test_the_admin_emails_a_sign_in_link_without_any_code(self):
        self.client.force_login(self.admin_a)
        response = self.start(self.emp_a)
        self.assertRedirects(response, reverse("person_detail", args=[self.emp_a.pk]))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.subject, SETUP_EMAIL_SUBJECT)
        self.assertEqual(message.to, [self.emp_a.email])
        self.assertIn("http://testserver" + reverse("login"), message.body)
        self.assertNotIn("[registration]", message.body)
        self.assertIsNone(CODE_PATTERN.search(message.body))
        self.assertFalse(RegistrationCode.objects.exists())

    def test_starting_setup_is_audited(self):
        self.client.force_login(self.admin_a)
        self.start(self.emp_a)
        record = AuditLog.objects.get(action="monitoring_setup.started")
        self.assertEqual((record.actor, record.organization, record.detail), (self.admin_a, self.org_a, "email sent"))

    def test_a_failed_email_is_reported_and_audited(self):
        self.client.force_login(self.admin_a)
        with mock.patch("devices.services.send_mail", side_effect=OSError("smtp down")):
            response = self.start(self.emp_a)
        self.assertRedirects(response, reverse("person_detail", args=[self.emp_a.pk]))
        self.assertEqual(AuditLog.objects.get(action="monitoring_setup.started").detail, "email failed")
        self.assertEqual(mail.outbox, [])

    def test_a_deactivated_employee_gets_no_email(self):
        self.emp_a.is_active = False
        self.emp_a.save(update_fields=["is_active"])
        self.client.force_login(self.admin_a)
        self.start(self.emp_a)
        self.assertEqual(mail.outbox, [])

    def test_another_organizations_employee_is_not_found(self):
        self.client.force_login(self.admin_a)
        self.assertEqual(self.start(self.emp_b).status_code, 404)
        self.assertEqual(mail.outbox, [])

    def test_only_admins_can_start_setup(self):
        for user in (self.manager_a, self.emp_a):
            with self.subTest(user=user.email):
                self.client.force_login(user)
                self.assertEqual(self.start(self.emp_a).status_code, 403)
        self.assertEqual(mail.outbox, [])


class ConnectCardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a = make_org("Org A")
        cls.emp_a = make_user("e@a.test", Role.EMPLOYEE, cls.org_a, "Employee A")
        cls.other = make_user("other@a.test", Role.EMPLOYEE, cls.org_a, "Other Employee")

    def dashboard(self):
        self.client.force_login(self.emp_a)
        return self.client.get(reverse("dashboard_me"))

    def test_a_new_employee_is_asked_to_get_a_code(self):
        response = self.dashboard()
        self.assertContains(response, "Install WinAgent on your PC.")
        self.assertNotContains(response, "Connect this PC")

    def test_a_registered_pc_that_is_not_seen_lately_is_device_offline(self):
        make_device(self.emp_a)
        response = self.dashboard()
        self.assertContains(response, "Device Offline")
        self.assertNotContains(response, "Connect this PC")

    def test_an_installed_pc_waits_for_admin_approval_with_no_start_button(self):
        from django.utils import timezone

        make_device(self.emp_a, last_seen=timezone.now())
        response = self.dashboard()
        self.assertContains(response, "WinAgent Installed – Waiting for Admin Approval")
        self.assertNotContains(response, "Monitoring Active")
        self.assertNotContains(response, "Approve &amp; Start Monitoring")

    def test_a_revoked_device_does_not_count_as_connected(self):
        make_device(self.emp_a, active=False)
        self.assertContains(self.dashboard(), "Install WinAgent on your PC.")

    def test_another_employees_device_does_not_change_my_card(self):
        from django.utils import timezone

        make_device(self.other, last_seen=timezone.now())
        self.assertContains(self.dashboard(), "Install WinAgent on your PC.")
