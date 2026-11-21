"""Admin "Start monitoring" for an employee whose PC is already approved."""
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from audit.models import AuditLog
from devices.models import MonitoringStart
from devices.tests.test_setup import make_device


class AdminStartTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.manager_a = make_user("m@a.test", Role.MANAGER, cls.org_a)
        cls.emp_a = make_user("e@a.test", Role.EMPLOYEE, cls.org_a, "Employee A")
        cls.emp_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b, "Employee B")

    def start(self, person):
        return self.client.post(reverse("admin_monitoring_start", args=[person.pk]))

    def test_the_admin_start_asks_the_employee_and_does_not_start_monitoring(self):
        device = make_device(self.emp_a)
        self.client.force_login(self.admin_a)
        response = self.start(self.emp_a)
        self.assertRedirects(response, reverse("person_detail", args=[self.emp_a.pk]))
        start = MonitoringStart.objects.get(device=device)
        self.assertEqual(start.status, MonitoringStart.Status.AWAITING_CONSENT)

    def test_the_audit_names_the_admin_who_asked(self):
        make_device(self.emp_a)
        self.client.force_login(self.admin_a)
        self.start(self.emp_a)
        entry = AuditLog.objects.get(action="monitoring.approval_requested")
        self.assertEqual(entry.actor, self.admin_a)

    def test_no_pc_means_a_clear_message_and_no_start(self):
        self.client.force_login(self.admin_a)
        response = self.client.post(reverse("admin_monitoring_start", args=[self.emp_a.pk]), follow=True)
        self.assertContains(response, "has no connected PC yet")
        self.assertFalse(MonitoringStart.objects.exists())

    def test_a_revoked_pc_does_not_count(self):
        make_device(self.emp_a, active=False)
        self.client.force_login(self.admin_a)
        self.start(self.emp_a)
        self.assertFalse(MonitoringStart.objects.exists())

    def test_only_admins_can_start_monitoring(self):
        make_device(self.emp_a)
        for user in (self.manager_a, self.emp_a):
            with self.subTest(user=user.email):
                self.client.force_login(user)
                self.assertEqual(self.start(self.emp_a).status_code, 403)
        self.assertFalse(MonitoringStart.objects.exists())

    def test_another_organizations_employee_is_not_found(self):
        make_device(self.emp_b)
        self.client.force_login(self.admin_a)
        self.assertEqual(self.start(self.emp_b).status_code, 404)
        self.assertFalse(MonitoringStart.objects.exists())

    def test_the_button_shows_only_when_a_pc_is_approved(self):
        self.client.force_login(self.admin_a)
        url = reverse("person_detail", args=[self.emp_a.pk])
        self.assertNotContains(self.client.get(url), "Start monitoring</button>")
        make_device(self.emp_a)
        self.assertContains(self.client.get(url), "Start monitoring</button>")
