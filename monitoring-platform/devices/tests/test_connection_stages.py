"""The employee dashboard shows the device connection steps and refreshes its status."""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from devices.pairing import create_pairing_request, open_setup_window

PC = {"device_name": "LAPTOP-7", "os_version": "Windows 11 10.0.26100", "agent_version": "0.1.0", "architecture": "x64"}


class ConnectionStagesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)
        cls.emp = make_user("e@a.test", Role.EMPLOYEE, cls.org)

    def test_before_the_admin_starts_the_first_step_is_current(self):
        self.client.force_login(self.emp)
        response = self.client.get(reverse("dashboard_me"))
        self.assertContains(response, 'id="device-status"')
        self.assertContains(response, 'class="current" aria-current="step">Download WinAgent')

    def test_a_waiting_pc_moves_the_current_step_to_approval(self):
        open_setup_window(self.admin, self.emp)
        create_pairing_request(PC, self.org)
        self.client.force_login(self.emp)
        response = self.client.get(reverse("dashboard_me"))
        self.assertContains(response, 'class="done"')
        self.assertContains(response, "Download WinAgent")
        self.assertContains(response, 'class="current" aria-current="step">Approve this PC')
