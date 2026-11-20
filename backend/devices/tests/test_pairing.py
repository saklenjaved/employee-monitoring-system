"""Connecting a PC without a code: setup window, waiting PCs, password approval, one-time credential."""
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from audit.models import AuditLog
from devices.models import Device, MonitoringStart, PairingRequest, SetupWindow
from devices.pairing import (
    approve_request,
    create_pairing_request,
    issue_enrollment_token,
    open_setup_window,
    poll_pairing,
    waiting_pcs_for,
)

PASSWORD = "a-long-test-password"
PAIRING_URL = "/api/agent/v1/pairing/requests/"
HEARTBEAT_URL = "/api/agent/v1/heartbeat/"
PC = {"device_name": "LAPTOP-7", "os_version": "Windows 11 10.0.26100", "agent_version": "0.1.0", "architecture": "x64"}


class SetupWindowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.emp_a = make_user("e@a.test", Role.EMPLOYEE, cls.org_a)
        cls.other_a = make_user("o@a.test", Role.EMPLOYEE, cls.org_a)
        cls.emp_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b)

    def test_opening_a_window_gives_the_employee_a_limited_time(self):
        window, error = open_setup_window(self.admin_a, self.emp_a)
        self.assertIsNone(error)
        self.assertEqual(window.user, self.emp_a)
        self.assertEqual(window.expires_at - window.opened_at, timedelta(minutes=settings.SETUP_WINDOW_MINUTES))
        self.assertTrue(AuditLog.objects.filter(action="setup_window.opened", target_id=str(window.id)).exists())

    def test_only_one_window_is_open_per_organization(self):
        open_setup_window(self.admin_a, self.emp_a)
        window, error = open_setup_window(self.admin_a, self.other_a)
        self.assertIsNone(window)
        self.assertEqual(error, "another_open")

    def test_an_expired_window_no_longer_blocks_a_new_one(self):
        now = timezone.now()
        open_setup_window(self.admin_a, self.emp_a, now=now)
        later = now + timedelta(minutes=settings.SETUP_WINDOW_MINUTES + 1)
        window, error = open_setup_window(self.admin_a, self.other_a, now=later)
        self.assertIsNone(error)
        self.assertEqual(window.user, self.other_a)

    def test_another_organization_can_open_its_own_window(self):
        open_setup_window(self.admin_a, self.emp_a)
        _, error = open_setup_window(make_user("admin@b.test", Role.ADMIN, self.org_b), self.emp_b)
        self.assertIsNone(error)

    def test_a_window_is_seen_only_by_its_employee(self):
        create_pairing_request(PC, self.org_a)
        open_setup_window(self.admin_a, self.emp_a)
        self.assertEqual(waiting_pcs_for(self.other_a).count(), 0)
        self.assertEqual(waiting_pcs_for(self.emp_a).count(), 1)

    def test_a_pc_of_another_organization_is_never_shown(self):
        open_setup_window(self.admin_a, self.emp_a)
        create_pairing_request(PC, self.org_b)
        self.assertEqual(waiting_pcs_for(self.emp_a).count(), 0)


class WaitingPcTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)
        cls.emp = make_user("e@a.test", Role.EMPLOYEE, cls.org)

    def test_no_window_means_no_waiting_pc(self):
        create_pairing_request(PC, self.org)
        self.assertEqual(waiting_pcs_for(self.emp).count(), 0)

    def test_a_pc_asking_during_the_window_is_shown(self):
        window, _ = open_setup_window(self.admin, self.emp)
        create_pairing_request(PC, self.org)
        self.assertEqual(waiting_pcs_for(self.emp).get().device_name, "LAPTOP-7")

    def test_an_expired_request_is_not_shown(self):
        now = timezone.now()
        open_setup_window(self.admin, self.emp, now=now)
        request, _ = create_pairing_request(PC, self.org, now=now)
        much_later = now + timedelta(minutes=settings.PAIRING_REQUEST_MINUTES + 1)
        self.assertEqual(waiting_pcs_for(self.emp, now=much_later).count(), 0)
        self.assertEqual(PairingRequest.objects.get(pk=request.pk).status, PairingRequest.Status.PENDING)

    def test_the_admin_click_opens_the_window_through_the_setup_view(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("monitoring_setup_start", args=[self.emp.pk]))
        self.assertTrue(SetupWindow.objects.filter(user=self.emp, closed_at__isnull=True).exists())


class ApprovalWebTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)
        cls.emp = make_user("e@a.test", Role.EMPLOYEE, cls.org)
        cls.emp.set_password(PASSWORD)
        cls.emp.save()
        cls.other = make_user("o@a.test", Role.EMPLOYEE, cls.org)

    def setUp(self):
        cache.clear()
        self.window, _ = open_setup_window(self.admin, self.emp)
        self.request, self.token = create_pairing_request(PC, self.org)
        self.client.force_login(self.emp)

    def approve(self, request=None, password=PASSWORD):
        request = request or self.request
        return self.client.post(
            reverse("pairing_approve", args=[request.pk]), {"current_password": password}, follow=True
        )

    def test_a_wrong_password_approves_nothing(self):
        response = self.approve(password="not-the-password")
        self.assertContains(response, "The password is not correct.")
        self.assertFalse(Device.objects.filter(name="LAPTOP-7").exists())
        self.assertEqual(PairingRequest.objects.get(pk=self.request.pk).status, PairingRequest.Status.PENDING)

    def test_the_right_password_creates_the_device_for_this_employee_without_starting_monitoring(self):
        response = self.approve()
        self.assertContains(response, "LAPTOP-7 is connected")
        device = Device.objects.get(name="LAPTOP-7")
        self.assertEqual(device.user, self.emp)
        self.assertEqual(device.organization, self.org)
        self.assertEqual(device.membership.user, self.emp)
        self.assertFalse(MonitoringStart.objects.filter(device=device).exists())  # the Admin and the employee start it later
        self.assertEqual(PairingRequest.objects.get(pk=self.request.pk).device, device)
        self.assertIsNotNone(SetupWindow.objects.get(pk=self.window.pk).closed_at)

    def test_the_approval_is_audited_without_the_password(self):
        self.approve()
        entry = AuditLog.objects.get(action="device.pairing_approved")
        self.assertNotIn(PASSWORD, str(entry.detail) + entry.actor_email)

    def test_a_pc_can_be_approved_only_once(self):
        self.approve()
        second = create_pairing_request(PC, self.org)[0]
        response = self.approve(request=second)
        self.assertContains(response, "The approval window is closed.")
        self.assertEqual(Device.objects.filter(user=self.emp).count(), 1)

    def test_a_pc_that_asks_again_while_waiting_keeps_one_request_and_gets_a_new_token(self):
        PairingRequest.objects.all().delete()
        first, first_token = create_pairing_request(PC, self.org)
        second, second_token = create_pairing_request(PC, self.org)
        self.assertEqual(first.pk, second.pk)
        self.assertNotEqual(first_token, second_token)
        self.assertEqual(PairingRequest.objects.filter(device_name=PC["device_name"]).count(), 1)
        response = self.client.get(reverse("dashboard_me"))
        self.assertEqual(response.content.decode().count("Approve this PC?"), 1)

    def test_another_employee_cannot_approve_this_pc(self):
        self.client.force_login(self.other)
        response = self.approve(password=PASSWORD)
        self.assertNotContains(response, "is connected")
        self.assertFalse(Device.objects.filter(name="LAPTOP-7").exists())

    def test_an_expired_window_cannot_approve(self):
        SetupWindow.objects.filter(pk=self.window.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        response = self.approve()
        self.assertContains(response, "The approval window is closed.")
        self.assertFalse(Device.objects.filter(name="LAPTOP-7").exists())

    def test_an_expired_pc_request_cannot_be_approved(self):
        PairingRequest.objects.filter(pk=self.request.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        response = self.approve()
        self.assertContains(response, "That PC is no longer waiting.")
        self.assertFalse(Device.objects.filter(name="LAPTOP-7").exists())

    def test_the_dashboard_shows_the_waiting_pc_with_a_password_box(self):
        response = self.client.get(reverse("dashboard_me"))
        self.assertContains(response, "Approve this PC?")
        self.assertContains(response, "LAPTOP-7")
        self.assertContains(response, 'name="current_password"')

    # --- The dashboard follows the connection by itself --------------------------------------

    def test_the_card_refreshes_every_few_seconds_while_a_pc_is_being_connected(self):
        response = self.client.get(reverse("dashboard_me"))
        self.assertContains(response, 'hx-trigger="every 4s')

    def test_the_card_refreshes_slowly_when_nothing_is_being_connected(self):
        SetupWindow.objects.filter(pk=self.window.pk).update(closed_at=timezone.now())
        response = self.client.get(reverse("dashboard_me"))
        self.assertContains(response, 'hx-trigger="every 20s')

    def test_the_approve_button_appears_in_the_refreshed_card_without_a_reload(self):
        # A fresh pairing request that arrives after the page loaded shows up in the next refresh.
        PairingRequest.objects.all().delete()
        first = self.client.get(reverse("dashboard_me"))
        self.assertNotContains(first, "Approve this PC?")
        create_pairing_request({**PC, "device_name": "NEW-PC"}, self.org)
        refreshed = self.client.get(reverse("dashboard_me"), HTTP_HX_REQUEST="true")
        self.assertContains(refreshed, "Approve this PC?")
        self.assertContains(refreshed, "NEW-PC")

    def test_after_approval_the_card_says_connecting_until_the_agent_checks_in(self):
        response = self.approve()
        self.assertContains(response, "Connecting")
        self.assertContains(response, "LAPTOP-7 is approved.")
        self.assertNotContains(response, "Approve this PC?")
        self.assertNotContains(response, "Device Offline")
        self.assertContains(response, 'hx-trigger="every 4s')

    def test_the_connecting_state_ends_when_the_agent_checks_in(self):
        self.approve()
        Device.objects.filter(name="LAPTOP-7").update(last_seen_at=timezone.now() + timedelta(seconds=5))
        response = self.client.get(reverse("dashboard_me"))
        self.assertNotContains(response, "LAPTOP-7 is approved.")
        self.assertContains(response, 'hx-trigger="every 20s')

    def test_a_pc_that_never_came_back_is_offline_again_after_a_while(self):
        self.approve()
        later = timezone.now() - timedelta(minutes=settings.PAIRING_REQUEST_MINUTES + 1)
        PairingRequest.objects.filter(pk=self.request.pk).update(approved_at=later)
        MonitoringStart.objects.filter(device__name="LAPTOP-7").update(expires_at=later)  # the start ran out too
        response = self.client.get(reverse("dashboard_me"))
        self.assertNotContains(response, "LAPTOP-7 is approved.")
        self.assertContains(response, "Device Offline")


class PollApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)
        cls.emp = make_user("e@a.test", Role.EMPLOYEE, cls.org)

    def setUp(self):
        cache.clear()
        self.api = APIClient()
        self.window, _ = open_setup_window(self.admin, self.emp)
        self.enrollment_token = issue_enrollment_token(self.org)

    def create(self):
        response = self.api.post(PAIRING_URL, {**PC, "org_token": self.enrollment_token}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def test_a_wrong_enrollment_token_is_refused(self):
        response = self.api.post(PAIRING_URL, {**PC, "org_token": "not-a-real-token"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(PairingRequest.objects.exists())

    def test_a_replaced_enrollment_token_no_longer_works(self):
        issue_enrollment_token(self.org)
        response = self.api.post(PAIRING_URL, {**PC, "org_token": self.enrollment_token}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_the_token_is_stored_only_as_a_hash(self):
        self.org.refresh_from_db()
        self.assertNotEqual(self.org.enrollment_token_hash, self.enrollment_token)
        self.assertNotIn(self.enrollment_token, self.org.enrollment_token_hash)

    def test_the_request_belongs_to_the_token_organization(self):
        body = self.create()
        self.assertEqual(PairingRequest.objects.get(pk=body["request_id"]).organization, self.org)

    def poll(self, body, token=None):
        url = f"{PAIRING_URL}{body['request_id']}/"
        token = body["poll_token"] if token is None else token
        return self.api.get(url, HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_a_pc_gets_a_request_and_is_told_it_is_pending(self):
        body = self.create()
        self.assertEqual(self.poll(body).json(), {"status": "pending"})

    def test_a_wrong_or_missing_token_reveals_nothing(self):
        body = self.create()
        self.assertEqual(self.poll(body, token="not-the-token").status_code, 404)
        self.assertEqual(self.api.get(f"{PAIRING_URL}{body['request_id']}/").status_code, 404)

    def test_after_approval_the_credential_is_handed_over_once_and_works(self):
        body = self.create()
        approve_request(self.emp, body["request_id"])
        first = self.poll(body).json()
        self.assertEqual(first["status"], "approved")
        self.assertTrue(first["credential"])
        self.assertEqual(Device.objects.get(pk=first["device_id"]).organization, self.org)

        again = self.poll(body).json()
        self.assertEqual(again, {"status": "delivered"})
        self.assertNotIn("credential", again)

        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {first['credential']}")
        heartbeat = self.api.post(
            HEARTBEAT_URL,
            {"agent_version": "0.1.0", "sent_at": "2026-11-03T10:00:00Z", "session_type": "console", "queue_depth": 0},
            format="json",
        )
        self.assertEqual(heartbeat.status_code, 200)
        self.assertIsNone(heartbeat.json()["start"])  # approval alone does not start monitoring

    def test_the_credential_stored_at_approval_is_not_usable_before_the_poll(self):
        body = self.create()
        approve_request(self.emp, body["request_id"])
        device = PairingRequest.objects.get(pk=body["request_id"]).device
        self.assertEqual(device.last_seen_at, None)
        self.assertNotEqual(device.credential_hash, "")

    def test_an_expired_request_polls_as_expired(self):
        body = self.create()
        PairingRequest.objects.filter(pk=body["request_id"]).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.poll(body).json(), {"status": "expired"})

    def test_the_pairing_request_does_not_store_the_poll_token(self):
        body = self.create()
        record = PairingRequest.objects.get(pk=body["request_id"])
        self.assertNotIn(body["poll_token"], record.poll_token_hash)
        self.assertEqual(len(record.poll_token_hash), 64)

    def test_poll_pairing_directly_returns_none_for_an_unknown_request(self):
        self.assertIsNone(poll_pairing("00000000-0000-0000-0000-000000000000", "x"))
