from datetime import timedelta

from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from analytics.tests.helpers import make_device
from devices.models import MonitoringStart
from devices.start import STATUS_AWAITING, give_consent, monitoring_statuses, request_approval


class ApprovalExpiryTests(TestCase):
    """An Admin's request waits for the employee. An unanswered request runs out, and a new click makes a fresh one."""

    def setUp(self):
        self.org = make_org("Org A")
        self.admin = make_user("admin@a.test", Role.ADMIN, self.org)
        self.emp = make_user("emp@a.test", Role.EMPLOYEE, self.org)
        self.device = make_device(self.emp, "pc-emp")
        self.now = timezone.now()
        self.device.last_seen_at = self.now
        self.device.save()

    def test_an_unanswered_request_expires_and_a_new_click_makes_a_fresh_one(self):
        first, _ = request_approval(self.emp, actor=self.admin, now=self.now)
        later = self.now + timedelta(seconds=settings.MONITORING_APPROVAL_TIMEOUT_SECONDS + 1)
        self.assertEqual(give_consent(self.emp, now=later), (None, "not_approved"))
        second, _ = request_approval(self.emp, actor=self.admin, now=later)
        self.assertNotEqual(first.pk, second.pk)
        first.refresh_from_db()
        self.assertEqual(first.status, MonitoringStart.Status.EXPIRED)
        self.assertEqual(second.status, MonitoringStart.Status.AWAITING_CONSENT)

    def test_an_expired_request_is_not_shown_to_the_employee(self):
        request_approval(self.emp, actor=self.admin, now=self.now)
        self.assertEqual(monitoring_statuses([self.device], self.now)[self.device.pk], STATUS_AWAITING)
        later = self.now + timedelta(seconds=settings.MONITORING_APPROVAL_TIMEOUT_SECONDS + 1)
        self.assertNotEqual(monitoring_statuses([self.device], later)[self.device.pk], STATUS_AWAITING)

    def test_an_approval_request_is_shown_even_when_the_pc_is_offline(self):
        self.device.last_seen_at = self.now - timedelta(hours=5)
        self.device.save()
        request_approval(self.emp, actor=self.admin, now=self.now)
        self.assertEqual(monitoring_statuses([self.device], self.now)[self.device.pk], STATUS_AWAITING)

    def test_the_employee_can_still_approve_within_the_window(self):
        request_approval(self.emp, actor=self.admin, now=self.now)
        start, reason = give_consent(self.emp, now=self.now + timedelta(hours=1))
        self.assertIsNone(reason)
        self.assertEqual(start.status, MonitoringStart.Status.PENDING)
