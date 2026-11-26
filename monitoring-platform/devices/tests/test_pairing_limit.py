"""The approval card (password box) on the Employee Dashboard needs the PC's pairing request to reach the server.

Pairing requests have their own rate limit, so earlier installs or code registrations do not block the next one.
"""
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from devices.pairing import issue_enrollment_token, open_setup_window

PAIRING_URL = "/api/agent/v1/pairing/requests/"
REGISTER_URL = "/api/agent/v1/devices/register/"
PC = {"device_name": "LAPTOP-7", "os_version": "Windows 11 10.0.26100", "agent_version": "0.1.0", "architecture": "x64"}
PASSWORD_BOX = 'name="current_password"'


class PairingLimitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Limit Org")
        cls.admin = make_user("admin@limit.test", Role.ADMIN, cls.org)
        cls.employee = make_user("emp@limit.test", Role.EMPLOYEE, cls.org)
        cls.token = issue_enrollment_token(cls.org)

    def setUp(self):
        cache.clear()
        self.api = APIClient()

    def ask(self, token=None):
        return self.api.post(PAIRING_URL, {**PC, "org_token": token or self.token}, format="json")

    def use_up_the_registration_limit(self):
        statuses = [self.api.post(REGISTER_URL, {}, format="json").status_code for _ in range(11)]
        self.assertEqual(statuses[-1], 429)  # code registration is still limited to 10 an hour

    def test_the_password_card_appears_after_many_earlier_attempts(self):
        open_setup_window(self.admin, self.employee)
        self.client.force_login(self.employee)
        self.assertNotContains(self.client.get(reverse("dashboard_me")), PASSWORD_BOX)  # the PC has not asked yet

        for _ in range(15):  # several installs in one hour, some with an old token
            self.assertEqual(self.ask("old-token").status_code, 400)
        self.use_up_the_registration_limit()
        self.assertEqual(self.ask().status_code, 201)

        response = self.client.get(reverse("dashboard_me"))
        self.assertContains(response, "Approve this PC?")
        self.assertContains(response, PASSWORD_BOX)
        self.assertContains(response, "LAPTOP-7")

    def test_pairing_requests_are_still_limited(self):
        statuses = [self.ask().status_code for _ in range(61)]
        self.assertEqual(statuses[:60], [201] * 60)
        self.assertEqual(statuses[60], 429)
