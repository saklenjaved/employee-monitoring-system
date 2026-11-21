"""Credential renewal without approval, and no duplicate devices when a PC is approved again."""
from datetime import timedelta

from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from devices.models import Device
from devices.pairing import approve_request, create_pairing_request, open_setup_window, poll_pairing

RENEW_URL = "/api/agent/v1/devices/credential/renew/"
PC = {"device_name": "LAPTOP-7", "os_version": "Windows 11 10.0.26100", "agent_version": "0.1.0", "architecture": "x64"}


def pair_and_collect(employee, admin, org):
    """Run the pairing flow end to end and return (device, credential)."""
    open_setup_window(admin, employee)
    request, token = create_pairing_request(PC, org)
    approve_request(employee, request.id)
    result = poll_pairing(request.id, token)
    return Device.objects.get(id=result["device_id"]), result["credential"]


class RenewalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)
        cls.employee = make_user("e@a.test", Role.EMPLOYEE, cls.org)

    def setUp(self):
        self.device, self.credential = pair_and_collect(self.employee, self.admin, self.org)
        self.client = APIClient()

    def auth(self, credential):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {credential}")

    def test_renewal_issues_a_new_credential_without_approval(self):
        self.auth(self.credential)
        response = self.client.post(RENEW_URL, {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.data["credential"], self.credential)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_renewed_credential_is_good_for_the_full_period(self):
        self.auth(self.credential)
        response = self.client.post(RENEW_URL, {}, format="json")
        self.device.refresh_from_db()
        self.assertGreater(self.device.credential_expires_at, timezone.now() + timedelta(days=settings.AGENT_CREDENTIAL_DAYS - 1))

    def test_the_old_credential_stops_working_after_renewal(self):
        self.auth(self.credential)
        new = self.client.post(RENEW_URL, {}, format="json").data["credential"]
        self.auth(self.credential)
        self.assertEqual(self.client.post(RENEW_URL, {}, format="json").status_code, 401)
        self.auth(new)
        self.assertEqual(self.client.post(RENEW_URL, {}, format="json").status_code, 200)

    def test_renewal_needs_a_credential(self):
        self.client.credentials()
        self.assertEqual(self.client.post(RENEW_URL, {}, format="json").status_code, 401)

    def test_a_revoked_device_cannot_renew(self):
        self.device.is_active = False
        self.device.save(update_fields=["is_active"])
        self.auth(self.credential)
        self.assertEqual(self.client.post(RENEW_URL, {}, format="json").status_code, 401)

    def test_get_is_not_allowed(self):
        self.auth(self.credential)
        self.assertEqual(self.client.get(RENEW_URL).status_code, 405)


class NoDuplicateDeviceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)
        cls.employee = make_user("e@a.test", Role.EMPLOYEE, cls.org)

    def test_approving_the_same_pc_again_reuses_its_device(self):
        first, _ = pair_and_collect(self.employee, self.admin, self.org)
        second, _ = pair_and_collect(self.employee, self.admin, self.org)
        self.assertEqual(first.id, second.id)
        self.assertEqual(Device.objects.filter(user=self.employee, name=PC["device_name"]).count(), 1)

    def test_the_reused_device_gets_a_working_new_credential(self):
        pair_and_collect(self.employee, self.admin, self.org)
        device, credential = pair_and_collect(self.employee, self.admin, self.org)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {credential}")
        self.assertEqual(client.post(RENEW_URL, {}, format="json").status_code, 200)

    def test_approving_a_revoked_pc_again_brings_it_back(self):
        first, _ = pair_and_collect(self.employee, self.admin, self.org)
        first.is_active = False
        first.save(update_fields=["is_active"])
        second, _ = pair_and_collect(self.employee, self.admin, self.org)
        second.refresh_from_db()
        self.assertEqual(first.id, second.id)
        self.assertTrue(second.is_active)
