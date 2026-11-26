"""The employee's own Attendance tab. It shows only the signed-in employee's data."""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from analytics.summaries import rebuild_day
from analytics.tests.test_employee_detail import at_local, today_in_display_zone
from analytics.tests.test_employee_live import add_sample
from devices.tests.test_setup import make_device


class MyAttendanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org T")
        cls.admin = make_user("admin@t.test", Role.ADMIN, cls.org)
        cls.me = make_user("me@t.test", Role.EMPLOYEE, cls.org, "Me Tabs")
        cls.other = make_user("other@t.test", Role.EMPLOYEE, cls.org, "Other Tabs")
        cls.my_device = make_device(cls.me, last_seen=timezone.now())
        cls.other_device = make_device(cls.other, last_seen=timezone.now())
        today = today_in_display_zone()
        add_sample(cls.my_device, "my-app.exe", "active", at_local(today, 10, 0))
        add_sample(cls.other_device, "other-app.exe", "active", at_local(today, 9, 0))
        rebuild_day(cls.me, today)
        rebuild_day(cls.other, today)
        cls.today = today

    def setUp(self):
        self.client.force_login(self.me)

    def test_the_attendance_page_is_my_day_only(self):
        response = self.client.get(reverse("my_attendance"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "10:00")
        self.assertNotContains(response, "09:00")
        self.assertNotContains(response, "other-app.exe")

    def test_the_date_filter_applies(self):
        yesterday = (self.today - timedelta(days=1)).isoformat()
        response = self.client.get(reverse("my_attendance"), {"start": yesterday, "end": yesterday})
        self.assertContains(response, "No activity recorded in these dates.")
        self.assertNotContains(response, "10:00")

    def test_the_sidebar_shows_attendance_for_an_employee(self):
        html = self.client.get(reverse("my_attendance")).content.decode()
        self.assertIn(f'href="{reverse("my_attendance")}"', html)
        self.assertIn('aria-current="page" title="Attendance"', html)
        self.assertNotIn('title="Activity timeline"', html)
        self.assertNotIn('title="Apps &amp; Websites"', html)

    def test_other_roles_cannot_open_attendance(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("my_attendance")).status_code, 403)

    def test_admins_do_not_see_the_employee_tab_in_the_sidebar(self):
        self.client.force_login(self.admin)
        html = self.client.get(reverse("dashboard_organization")).content.decode()
        self.assertNotIn(f'href="{reverse("my_attendance")}"', html)
