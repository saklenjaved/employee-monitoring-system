from datetime import datetime, timezone

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from analytics.tests.helpers import make_device
from monitoring.models import MonitoringPolicy
from screenshots.models import ScreenshotStatus
from screenshots.tests.test_pages import make_shot

UTC = timezone.utc


class ScreenshotTimezoneTests(TestCase):
    """Times on the screenshot pages and the employee profile use the organization's zone, not a fixed one."""

    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Kolkata Org")
        MonitoringPolicy.objects.create(organization=cls.org, timezone="Asia/Kolkata")
        cls.admin = make_user("admin@kolkata.test", Role.ADMIN, cls.org)
        cls.employee = make_user("emp@kolkata.test", Role.EMPLOYEE, cls.org, full_name="Emp Person")
        device = make_device(cls.employee)
        # 09:00 UTC is 14:30 in India (UTC+5:30).
        cls.shot = make_shot(cls.employee, device, datetime(2026, 11, 23, 9, 0, tzinfo=UTC), ScreenshotStatus.UPLOADED)

    def setUp(self):
        self.client.force_login(self.admin)

    def test_detail_page_shows_organization_zone(self):
        response = self.client.get(reverse("screenshot_detail", args=[self.shot.pk]))
        self.assertContains(response, "23 Nov 2026 14:30:00 IST")
        self.assertNotContains(response, "09:00:00")

    def test_list_shows_organization_zone(self):
        response = self.client.get(reverse("screenshot_list"))
        self.assertContains(response, "23 Nov 2026, 14:30 IST")

    def test_profile_recent_screenshots_show_organization_zone(self):
        # The employee page shows the screenshots of the chosen dates, so pick the day of this screenshot.
        response = self.client.get(
            reverse("dashboard_employee", args=[self.employee.pk]), {"start": "2026-11-23", "end": "2026-11-23"}
        )
        self.assertContains(response, "23 Nov 2026, 14:30:00 IST")
        self.assertNotContains(response, "23 Nov 2026, 09:00:00")
