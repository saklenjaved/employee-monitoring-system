"""The Admin's monitoring duration decides how long a confirmed start collects, and the start ends when it runs out."""
from datetime import timedelta

from django.utils import timezone
from django.test import TestCase

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from devices.models import MonitoringStart
from devices.start import confirm_start, request_start, start_instruction
from devices.tests.test_setup import make_device
from screenshots.models import DEFAULT_MONITORING_MINUTES
from screenshots.services import update_policy


class MonitoringDurationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)
        cls.employee = make_user("emp@a.test", Role.EMPLOYEE, cls.org)
        cls.device = make_device(cls.employee, last_seen=timezone.now())

    def confirmed_start(self, now):
        start, _ = request_start(self.employee, now=now)
        confirm_start(self.device, str(start.id), now=now)
        return MonitoringStart.objects.get(pk=start.pk)

    def test_without_a_saved_setting_the_starting_duration_applies(self):
        now = timezone.now()
        start = self.confirmed_start(now)
        self.assertEqual(start.authorized_until - now, timedelta(minutes=DEFAULT_MONITORING_MINUTES))

    def test_the_admins_duration_is_used_for_the_start(self):
        update_policy(self.admin, self.org, {"monitoring_minutes": 30})
        now = timezone.now()
        start = self.confirmed_start(now)
        self.assertEqual(start.authorized_until - now, timedelta(minutes=30))

    def test_the_agent_is_told_the_admins_duration(self):
        update_policy(self.admin, self.org, {"monitoring_minutes": 90})
        start, _ = request_start(self.employee)
        instruction = start_instruction(self.device)
        self.assertEqual(instruction["id"], str(start.id))
        self.assertEqual(instruction["authorized_seconds"], 90 * 60)

    def test_a_running_start_stops_being_handed_out_when_its_duration_runs_out(self):
        update_policy(self.admin, self.org, {"monitoring_minutes": 30})
        now = timezone.now()
        self.confirmed_start(now)
        self.assertIsNotNone(start_instruction(self.device, now=now + timedelta(minutes=29)))
        self.assertIsNone(start_instruction(self.device, now=now + timedelta(minutes=31)))

    def test_the_duration_cannot_be_set_outside_the_allowed_range(self):
        from screenshots.forms import ScreenshotSettingsForm

        for minutes in (5, 2000):
            form = ScreenshotSettingsForm(
                data={"interval_seconds": 600, "retention_days": 30, "blur_level": "", "monitoring_minutes": minutes}
            )
            self.assertFalse(form.is_valid())
            self.assertIn("monitoring_minutes", form.errors)
