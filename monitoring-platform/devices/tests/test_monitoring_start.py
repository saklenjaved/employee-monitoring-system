"""Starting monitoring from the dashboard. Monitoring counts as started only when the agent confirms it."""
from datetime import timedelta
from io import StringIO

from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from audit.models import AuditLog
from devices.models import Device, MonitoringStart
from devices.pairing import issue_install_token, open_window_for
from devices.tests.test_event_ingestion import EVENTS_URL, make_event
from monitoring.models import MonitoringEvent
from devices.start import (
    STATUS_ACTIVE,
    STATUS_ERROR,
    STATUS_NOT_CONNECTED,
    STATUS_OFFLINE,
    STATUS_PAUSED,
    STATUS_READY,
    STATUS_REQUESTED,
    STATUS_STARTING,
    confirm_start,
    monitoring_status,
    request_start,
    start_instruction,
)

from .test_agent_api import HEARTBEAT_URL, AgentApiTestCase, heartbeat_body
from .test_setup import make_device


class StatusTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.emp_a = make_user("e@a.test", Role.EMPLOYEE, cls.org_a)
        cls.emp_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b)

    def test_an_employee_without_a_pc_is_not_connected(self):
        self.assertEqual(monitoring_status(self.emp_a), STATUS_NOT_CONNECTED)

    def test_a_seen_pc_is_ready_to_start(self):
        make_device(self.emp_a, last_seen=timezone.now())
        self.assertEqual(monitoring_status(self.emp_a), STATUS_READY)

    def test_a_pc_not_seen_lately_is_device_offline(self):
        make_device(self.emp_a, last_seen=timezone.now() - timedelta(hours=2))
        self.assertEqual(monitoring_status(self.emp_a), STATUS_OFFLINE)

    def test_a_waiting_start_is_starting(self):
        device = make_device(self.emp_a, last_seen=timezone.now())
        request_start(self.emp_a)
        self.assertEqual(monitoring_status(self.emp_a), STATUS_STARTING)
        self.assertEqual(MonitoringStart.objects.get().device, device)

    def test_monitoring_is_active_only_after_the_agent_confirms(self):
        device = make_device(self.emp_a, last_seen=timezone.now(), policy_version="p1")
        start, _ = request_start(self.emp_a)
        device.monitoring_state = "active"
        device.save()
        self.assertEqual(monitoring_status(self.emp_a), STATUS_STARTING)
        confirm_start(device, start.id)
        self.assertEqual(monitoring_status(self.emp_a), STATUS_ACTIVE)

    def test_a_start_the_agent_never_confirmed_shows_error_once_it_expires(self):
        make_device(self.emp_a, last_seen=timezone.now())
        request_start(self.emp_a)
        later = timezone.now() + timedelta(seconds=settings.MONITORING_START_TIMEOUT_SECONDS + 1)
        self.assertEqual(monitoring_status(self.emp_a, now=later), STATUS_ERROR)

    def test_an_agent_error_code_shows_error(self):
        device = make_device(self.emp_a, last_seen=timezone.now())
        device.last_error_code = "ConnectionError"
        device.save()
        self.assertEqual(monitoring_status(self.emp_a), STATUS_ERROR)

    def test_another_organizations_pc_is_never_shown(self):
        make_device(self.emp_b, last_seen=timezone.now())
        self.assertEqual(monitoring_status(self.emp_a), STATUS_NOT_CONNECTED)

    def test_a_second_request_while_one_waits_returns_the_same_request(self):
        make_device(self.emp_a, last_seen=timezone.now())
        first, _ = request_start(self.emp_a)
        second, _ = request_start(self.emp_a)
        self.assertEqual(first.id, second.id)
        self.assertEqual(MonitoringStart.objects.count(), 1)

    def test_starting_without_a_pc_says_no_pc(self):
        self.assertEqual(request_start(self.emp_a), (None, "no_pc"))

    def test_requesting_a_start_and_confirming_it_are_audited(self):
        device = make_device(self.emp_a, last_seen=timezone.now())
        start, _ = request_start(self.emp_a)
        confirm_start(device, start.id)
        self.assertTrue(AuditLog.objects.filter(action="monitoring.start_requested", target_id=str(start.id)).exists())
        self.assertTrue(AuditLog.objects.filter(action="monitoring.started", target_id=str(start.id)).exists())


class HeartbeatStartTests(AgentApiTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.registered = self.register()
        self.authenticate(self.registered["credential"])
        self.device_id = self.registered["device_id"]

    def heartbeat(self, **overrides):
        response = self.api.post(HEARTBEAT_URL, heartbeat_body(**overrides), format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_without_a_start_the_agent_is_told_nothing(self):
        self.assertIsNone(self.heartbeat(monitoring_state="awaiting_start")["start"])

    def test_a_pending_start_is_handed_to_the_agent_and_confirmed_when_it_collects(self):
        start, _ = request_start(self.emp_a)
        reply = self.heartbeat(monitoring_state="awaiting_start")
        self.assertEqual(reply["start"]["id"], str(start.id))
        start.refresh_from_db()
        self.assertEqual(start.status, MonitoringStart.Status.PENDING)

        reply = self.heartbeat(monitoring_state="active", monitoring_start_id=str(start.id))
        start.refresh_from_db()
        self.assertEqual(start.status, MonitoringStart.Status.CONFIRMED)
        self.assertEqual(reply["start"]["id"], str(start.id))
        self.assertLessEqual(reply["start"]["authorized_seconds"], 480 * 60)  # the default duration, set by the Admin
        self.assertGreater(reply["start"]["authorized_seconds"], 0)

    def test_active_without_the_start_id_does_not_confirm(self):
        start, _ = request_start(self.emp_a)
        self.heartbeat(monitoring_state="active")
        start.refresh_from_db()
        self.assertEqual(start.status, MonitoringStart.Status.PENDING)

    def test_a_start_id_for_another_pc_does_not_confirm(self):
        start, _ = request_start(self.emp_a)
        other_pc = make_device(self.emp_a)
        other_start = MonitoringStart.objects.create(
            organization=self.org_a, user=self.emp_a, device=other_pc,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        self.heartbeat(monitoring_state="active", monitoring_start_id=str(other_start.id))
        start.refresh_from_db()
        self.assertEqual(start.status, MonitoringStart.Status.PENDING)

    def test_an_expired_start_is_not_confirmed_or_handed_out(self):
        start, _ = request_start(self.emp_a)
        MonitoringStart.objects.filter(pk=start.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        reply = self.heartbeat(monitoring_state="active", monitoring_start_id=str(start.id))
        start.refresh_from_db()
        self.assertEqual(start.status, MonitoringStart.Status.PENDING)
        self.assertIsNone(reply["start"])

    def test_a_confirmed_start_stops_when_its_time_is_over(self):
        start, _ = request_start(self.emp_a)
        self.heartbeat(monitoring_state="active", monitoring_start_id=str(start.id))
        MonitoringStart.objects.filter(pk=start.pk).update(authorized_until=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(self.heartbeat(monitoring_state="active", monitoring_start_id=str(start.id))["start"])

    def test_the_start_instruction_is_none_for_a_device_with_no_start(self):
        self.assertIsNone(start_instruction(Device.objects.get(pk=self.device_id)))


class FullMonitoringWorkflowTests(TestCase):
    """The whole path through the real endpoints: Admin setup, pairing, approval, Admin start, the agent's
    heartbeat, its confirmation, event upload, daily totals, and what the Admin dashboard shows."""

    PASSWORD = "a-long-test-password"
    PC = {"device_name": "IRFAN-PC", "os_version": "Windows 11", "agent_version": "0.1.0", "architecture": "x64"}

    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)
        cls.emp = make_user("e@a.test", Role.EMPLOYEE, cls.org, "Irfan Employee")
        cls.emp.set_password(cls.PASSWORD)
        cls.emp.save()

    def setUp(self):
        cache.clear()
        self.agent = APIClient()

    # --- steps -----------------------------------------------------------------------------------

    def pair_and_approve(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("monitoring_setup_start", args=[self.emp.pk]))  # Admin: Connect a new PC
        token = issue_install_token(open_window_for(self.emp))  # what the employee's download carries
        created = self.agent.post("/api/agent/v1/pairing/requests/", {**self.PC, "org_token": token}, format="json")
        self.assertEqual(created.status_code, 201, created.content)
        request_id, poll_token = created.json()["request_id"], created.json()["poll_token"]
        self.client.force_login(self.emp)  # Employee: Approve with password
        self.client.post(reverse("pairing_approve", args=[request_id]), {"current_password": self.PASSWORD})
        polled = self.agent.get(
            f"/api/agent/v1/pairing/requests/{request_id}/", HTTP_AUTHORIZATION=f"Bearer {poll_token}"
        ).json()
        self.assertEqual(polled["status"], "approved")
        self.agent.credentials(HTTP_AUTHORIZATION=f"Bearer {polled['credential']}")
        return Device.objects.get(pk=polled["device_id"])

    def heartbeat(self, state, start_id=None, expect=200):
        body = heartbeat_body(monitoring_state=state, queue_depth=0, focus_app="code.exe", focus_title="")
        if start_id:
            body["monitoring_start_id"] = start_id
        response = self.agent.post(HEARTBEAT_URL, body, format="json")
        self.assertEqual(response.status_code, expect, response.content)
        return response.json() if expect == 200 else None

    def admin_start(self):
        self.client.force_login(self.admin)
        return self.client.post(reverse("admin_monitoring_start", args=[self.emp.pk]), follow=True)

    def employee_consents(self):
        self.client.force_login(self.emp)
        return self.client.post(reverse("monitoring_start"), follow=True)

    def send_events(self):
        events = [make_event("app_focus", {"process_name": "code.exe", "duration_seconds": 30}), make_event("activity_sample")]
        response = self.agent.post(EVENTS_URL, {"events": events}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def admin_dashboard(self):
        self.client.force_login(self.admin)
        today = timezone.now().date().isoformat()
        return self.client.get(reverse("dashboard_organization"), {"start": today, "end": today})

    # --- the workflow ------------------------------------------------------------------------------

    def test_admin_start_reaches_the_agent_which_collects_and_the_admin_sees_the_data(self):
        device = self.pair_and_approve()
        self.assertEqual((device.user, device.organization, device.is_active), (self.emp, self.org, True))
        # Approving the PC does not start monitoring: nothing is handed to the agent yet.
        self.assertIsNone(self.heartbeat("awaiting_start")["start"])
        self.assertEqual(monitoring_status(self.emp), STATUS_READY)

        page = self.admin_start()  # Admin: Start monitoring asks for the employee's consent first
        self.assertContains(page, "Approval sent")
        self.assertTrue(MonitoringStart.objects.filter(device=device, status=MonitoringStart.Status.AWAITING_CONSENT).exists())
        self.employee_consents()  # Employee: Approve & Start Monitoring
        start = MonitoringStart.objects.get(device=device, status=MonitoringStart.Status.PENDING, expires_at__gt=timezone.now())
        self.assertEqual((start.user, start.organization), (self.emp, self.org))
        self.assertEqual(monitoring_status(self.emp), STATUS_STARTING)
        self.assertNotContains(self.admin_dashboard(), "Monitoring active</span>")  # a click alone is not active

        handed = self.heartbeat("awaiting_start")["start"]  # the agent's next check-in receives it
        self.assertEqual(handed["id"], str(start.id))
        self.heartbeat("active", start_id=handed["id"])  # the agent collects and confirms
        start.refresh_from_db()
        self.assertEqual(start.status, MonitoringStart.Status.CONFIRMED)
        self.assertEqual(monitoring_status(self.emp), STATUS_ACTIVE)

        ack = self.send_events()  # real data
        self.assertEqual(len(ack["accepted"]), 2)
        self.assertEqual(MonitoringEvent.objects.filter(user=self.emp, organization=self.org).count(), 2)
        call_command("build_summaries", stdout=StringIO())

        response = self.admin_dashboard()
        self.assertEqual(response.context["command"]["monitoring"]["monitored_now"], 1)
        row = next(r for r in response.context["monitoring_rows"] if r["user"] == self.emp)
        self.assertEqual(row["live"]["monitoring"], STATUS_ACTIVE)
        self.assertEqual(row["live"]["focus_app"], "code.exe")
        self.assertEqual(row["app_seconds"], 30)
        self.assertIn("code.exe", [item["name"] for item in response.context["report"]["apps"]])
        self.assertContains(response, "Monitoring active</span>")

    def test_a_start_sent_outside_hours_is_kept_as_paused_and_never_shown_active(self):
        device = self.pair_and_approve()
        self.heartbeat("awaiting_start")
        self.admin_start()
        self.employee_consents()
        start = MonitoringStart.objects.get(device=device, status=MonitoringStart.Status.PENDING)
        handed = self.heartbeat("awaiting_start")["start"]
        self.heartbeat("paused_hours", start_id=handed["id"])  # outside monitoring hours
        start.refresh_from_db()
        self.assertEqual(start.status, MonitoringStart.Status.CONFIRMED)  # kept; it does not run out
        self.assertEqual(monitoring_status(self.emp), STATUS_PAUSED)
        self.assertNotContains(self.admin_dashboard(), "Monitoring active</span>")
        self.heartbeat("active", start_id=handed["id"])  # the hours open
        self.assertEqual(monitoring_status(self.emp), STATUS_ACTIVE)

    def test_clicking_start_twice_while_one_waits_makes_one_request(self):
        device = self.pair_and_approve()
        self.heartbeat("awaiting_start")  # the PC has checked in, so it counts as registered
        self.admin_start()
        self.admin_start()
        self.assertEqual(MonitoringStart.objects.filter(device=device, status=MonitoringStart.Status.AWAITING_CONSENT).count(), 1)
        self.employee_consents()
        self.employee_consents()  # a double click does not make a second start
        self.assertEqual(MonitoringStart.objects.filter(device=device, status=MonitoringStart.Status.PENDING).count(), 1)

    def test_an_offline_pc_shows_connection_lost_and_a_waiting_start_is_delivered_when_it_returns(self):
        device = self.pair_and_approve()
        self.heartbeat("awaiting_start")
        Device.objects.filter(pk=device.pk).update(
            last_seen_at=timezone.now() - timedelta(seconds=settings.DEVICE_ONLINE_SECONDS + 60)
        )
        self.assertEqual(monitoring_status(self.emp), STATUS_OFFLINE)  # connection lost
        self.admin_start()  # started while offline
        self.employee_consents()
        self.assertEqual(monitoring_status(self.emp), STATUS_REQUESTED)
        handed = self.heartbeat("awaiting_start")["start"]  # the PC is back
        self.assertIsNotNone(handed)
        self.heartbeat("active", start_id=handed["id"])
        self.assertEqual(monitoring_status(self.emp), STATUS_ACTIVE)

    def test_a_revoked_or_wrong_credential_is_refused_and_never_confirms(self):
        device = self.pair_and_approve()
        self.heartbeat("awaiting_start")
        self.admin_start()
        self.employee_consents()
        start = MonitoringStart.objects.get(device=device, status=MonitoringStart.Status.PENDING)
        Device.objects.filter(pk=device.pk).update(is_active=False)
        self.heartbeat("active", start_id=str(start.id), expect=401)
        self.agent.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-credential")
        self.heartbeat("active", start_id=str(start.id), expect=401)
        start.refresh_from_db()
        self.assertEqual(start.status, MonitoringStart.Status.PENDING)


class EmployeeConsentTests(TestCase):
    """Monitoring starts only when the Admin approved it AND the employee confirmed it on their own PC."""

    def setUp(self):
        from accounts.tests.factories import make_org, make_user
        from devices.tests.test_setup import make_device

        self.org = make_org("Org A")
        self.admin = make_user("admin@a.test", Role.ADMIN, self.org)
        self.emp = make_user("e@a.test", Role.EMPLOYEE, self.org, "Employee A")
        self.other = make_user("o@a.test", Role.EMPLOYEE, self.org, "Other A")
        self.make_device = make_device
        self.device = make_device(self.emp, last_seen=timezone.now())

    def test_no_approval_means_no_consent(self):
        from devices.start import give_consent

        self.assertEqual(give_consent(self.emp), (None, "not_approved"))
        self.assertFalse(MonitoringStart.objects.exists())

    def test_an_unseen_pc_cannot_be_confirmed_yet(self):
        from devices.start import give_consent, request_approval

        Device.objects.filter(pk=self.device.pk).update(last_seen_at=None)
        request_approval(self.emp, actor=self.admin)
        self.assertEqual(give_consent(self.emp), (None, "not_registered"))
        self.assertTrue(MonitoringStart.objects.filter(status=MonitoringStart.Status.AWAITING_CONSENT).exists())

    def test_admin_approval_then_employee_consent_starts_it(self):
        from devices.start import give_consent, request_approval

        request_approval(self.emp, actor=self.admin)
        start, reason = give_consent(self.emp)
        self.assertIsNone(reason)
        self.assertEqual((start.status, start.device), (MonitoringStart.Status.PENDING, self.device))

    def test_a_double_confirmation_is_not_an_error_and_makes_one_start(self):
        from devices.start import give_consent, request_approval

        request_approval(self.emp, actor=self.admin)
        give_consent(self.emp)
        _, reason = give_consent(self.emp)
        self.assertEqual(reason, "already_started")
        self.assertEqual(MonitoringStart.objects.filter(status=MonitoringStart.Status.PENDING).count(), 1)

    def test_another_employee_cannot_confirm_for_this_employee(self):
        from devices.start import give_consent, request_approval

        request_approval(self.emp, actor=self.admin)
        self.assertEqual(give_consent(self.other), (None, "no_pc"))
        self.assertFalse(MonitoringStart.objects.filter(status=MonitoringStart.Status.PENDING).exists())

    def test_the_admin_cannot_confirm_for_the_employee(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("admin_monitoring_start", args=[self.emp.pk]))
        response = self.client.post(reverse("monitoring_start"))
        self.assertEqual(response.status_code, 403)  # only the employee can confirm
        self.assertFalse(MonitoringStart.objects.filter(status=MonitoringStart.Status.PENDING).exists())

    def test_employee_dashboard_shows_the_approval_button_only_after_the_admin_asks(self):
        self.client.force_login(self.emp)
        before = self.client.get(reverse("dashboard_me"))
        self.assertNotContains(before, "Approve &amp; Start Monitoring")
        self.client.force_login(self.admin)
        self.client.post(reverse("admin_monitoring_start", args=[self.emp.pk]))
        self.client.force_login(self.emp)
        after = self.client.get(reverse("dashboard_me"))
        self.assertContains(after, "Admin Approval Required")
        self.assertContains(after, "Approve &amp; Start Monitoring")
