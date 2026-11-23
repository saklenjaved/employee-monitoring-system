"""The employee dashboard shows the device connection steps and refreshes its status."""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from devices.models import MonitoringStart
from devices.pairing import create_pairing_request, open_setup_window
from devices.tests.test_setup import make_device

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

    def test_after_the_download_the_card_waits_for_winagent_and_refreshes_quickly(self):
        open_setup_window(self.admin, self.emp)
        self.client.force_login(self.emp)
        response = self.client.get(reverse("dashboard_me"))
        self.assertContains(response, "Waiting for WinAgent.")
        self.assertContains(response, 'hx-trigger="every 4s')
        self.assertContains(response, 'class="current" aria-current="step">Install WinAgent')

    def step_four(self, agent_state):
        """The dashboard after the agent confirmed a start and reported `agent_state`."""
        now = timezone.now()
        device = make_device(self.emp, last_seen=now)
        device.monitoring_state = agent_state
        device.save(update_fields=["monitoring_state"])
        MonitoringStart.objects.create(
            organization=self.org, user=self.emp, device=device, status=MonitoringStart.Status.CONFIRMED,
            expires_at=now + timedelta(minutes=5), confirmed_at=now, authorized_until=now + timedelta(hours=8),
        )
        self.client.force_login(self.emp)
        return self.client.get(reverse("dashboard_me"))

    def test_step_four_turns_green_once_the_agent_confirms_it_is_collecting(self):
        self.assertContains(self.step_four("active"), '<li data-step="4" class="done">PC connected')

    def test_step_four_turns_green_when_the_agent_confirmed_but_is_paused(self):
        self.assertContains(self.step_four("paused_hours"), '<li data-step="4" class="done">PC connected')

    def test_a_newer_pending_start_does_not_hide_a_running_confirmed_agent(self):
        now = timezone.now()
        MonitoringStart.objects.create(  # created by a second click; the agent only sees it at its next check-in
            organization=self.org, user=self.emp, device=make_device(self.emp, last_seen=now), expires_at=now + timedelta(minutes=5)
        )
        device = self.emp.devices.get()
        device.monitoring_state = "active"
        device.save(update_fields=["monitoring_state"])
        MonitoringStart.objects.create(
            organization=self.org, user=self.emp, device=device, status=MonitoringStart.Status.CONFIRMED,
            expires_at=now + timedelta(minutes=5), confirmed_at=now, authorized_until=now + timedelta(hours=8),
        )
        self.client.force_login(self.emp)
        self.assertContains(self.client.get(reverse("dashboard_me")), '<li data-step="4" class="done">PC connected')

    def test_step_four_stays_pending_until_the_agent_confirms(self):
        now = timezone.now()
        device = make_device(self.emp, last_seen=now)
        MonitoringStart.objects.create(
            organization=self.org, user=self.emp, device=device, expires_at=now + timedelta(minutes=5)
        )
        self.client.force_login(self.emp)
        response = self.client.get(reverse("dashboard_me"))
        self.assertContains(response, '<li data-step="4" class="current" aria-current="step">PC connected')

    def test_a_waiting_pc_moves_the_current_step_to_approval(self):
        open_setup_window(self.admin, self.emp)
        create_pairing_request(PC, self.org)
        self.client.force_login(self.emp)
        response = self.client.get(reverse("dashboard_me"))
        self.assertContains(response, 'class="done"')
        self.assertContains(response, "Download WinAgent")
        self.assertContains(response, 'class="current" aria-current="step">Approve this PC')
