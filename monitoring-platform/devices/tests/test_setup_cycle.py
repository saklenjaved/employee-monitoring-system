"""The whole setup cycle: download, pair, approve, and a revoke or deactivate that starts it over."""
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from devices.models import PairingRequest, SetupWindow
from devices.pairing import issue_install_token, open_setup_window, waiting_pcs_for
from devices.services import revoke_device
from devices.tests.test_setup import make_device
from workforce.services import set_person_active

DETAILS = {"device_name": "LAPTOP-7", "os_version": "Windows 11 10.0.26100", "agent_version": "0.1.0", "architecture": "x64"}


class SetupCycleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)
        cls.emp = make_user("e@a.test", Role.EMPLOYEE, cls.org)

    def pair_with(self, token):
        """What the installed agent does: it sends its install token (from its file name) as org_token."""
        return self.client.post(reverse("agent_pairing_request"), {**DETAILS, "org_token": token}, content_type="application/json")

    def download(self):
        window, _ = open_setup_window(self.admin, self.emp)
        return issue_install_token(window)

    def test_a_revoke_stops_the_old_agent_from_asking_to_connect_again(self):
        token = self.download()
        self.assertEqual(self.pair_with(token).status_code, 201)
        device = make_device(self.emp, last_seen=timezone.now())
        revoke_device(self.admin, device)
        # The old installation still has its token, but the reset closed it.
        self.assertEqual(self.pair_with(token).status_code, 400)
        self.assertFalse(waiting_pcs_for(self.emp).exists())

    def test_a_revoke_closes_the_window_and_expires_the_waiting_pc(self):
        self.download()
        create = self.client.post(reverse("agent_pairing_request"), {**DETAILS, "org_token": "x"}, content_type="application/json")
        self.assertEqual(create.status_code, 400)  # no token, no request
        device = make_device(self.emp, last_seen=timezone.now())
        revoke_device(self.admin, device)
        self.assertEqual(SetupWindow.objects.filter(user=self.emp, closed_at__isnull=True).count(), 0)
        self.assertEqual(PairingRequest.objects.filter(organization=self.org, status=PairingRequest.Status.PENDING).count(), 0)

    def test_after_a_revoke_a_new_download_starts_the_setup_again(self):
        old_token = self.download()
        device = make_device(self.emp, last_seen=timezone.now())
        revoke_device(self.admin, device)
        new_token = self.download()
        self.assertEqual(self.pair_with(old_token).status_code, 400)
        self.assertEqual(self.pair_with(new_token).status_code, 201)

    def test_after_a_revoke_the_dashboard_offers_the_download_again(self):
        self.download()
        device = make_device(self.emp, last_seen=timezone.now())
        revoke_device(self.admin, device)
        self.client.force_login(self.emp)
        response = self.client.get(reverse("dashboard_me"))
        self.assertContains(response, "Download WinAgent setup")
        self.assertNotContains(response, "Waiting for WinAgent")

    def test_deactivate_and_reactivate_start_the_setup_over(self):
        token = self.download()
        self.pair_with(token)
        make_device(self.emp, last_seen=timezone.now())
        set_person_active(self.admin, self.emp, False)
        set_person_active(self.admin, self.emp, True)
        self.assertEqual(self.pair_with(token).status_code, 400)
        self.assertFalse(SetupWindow.objects.filter(user=self.emp, closed_at__isnull=True).exists())
