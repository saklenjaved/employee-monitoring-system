"""The same employee's numbers agree on the Admin dashboard, the Employee dashboard and the chart.

The events are real rows; the totals come from build_summaries (rebuild_recent), not from fixtures.
Days are Indian Standard Time calendar days.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.test import TestCase

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from analytics.services import daily_trend, organization_report, usage_for
from analytics.summaries import rebuild_recent
from analytics.tests.helpers import activity, app, make_device

IST = ZoneInfo("Asia/Kolkata")


def ist(day, hour, minute=0, second=0):
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=IST)


class DashboardAgreementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org")
        cls.admin = make_user("admin@org.test", Role.ADMIN, cls.org)
        cls.employee = make_user("e@org.test", Role.EMPLOYEE, cls.org, "Employee")
        cls.device = make_device(cls.employee)
        cls.day = date(2026, 11, 2)
        # 2 Nov IST: two app samples of 60 s, three active samples of 30 s, one idle sample of 30 s.
        app(cls.device, ist(cls.day, 10, 0, 0), "chrome.exe", seconds=60)
        app(cls.device, ist(cls.day, 10, 5, 0), "notepad.exe", seconds=60)
        activity(cls.device, ist(cls.day, 10, 0, 30), "active", seconds=30)
        activity(cls.device, ist(cls.day, 10, 1, 0), "active", seconds=30)
        activity(cls.device, ist(cls.day, 10, 2, 0), "active", seconds=30)
        activity(cls.device, ist(cls.day, 10, 3, 0), "idle", seconds=30)
        rebuild_recent(since_hours=1)

    def test_the_stored_totals_match_the_recorded_events(self):
        page = usage_for(self.admin, self.employee, self.day, self.day)
        self.assertEqual(page["app_seconds"], 120)
        self.assertEqual(page["active_seconds"], 90)
        self.assertEqual(page["idle_seconds"], 30)

    def test_the_admin_row_and_the_employee_page_show_the_same_numbers(self):
        admin_report = organization_report(self.admin, self.day, self.day)
        row = next(r for r in admin_report["employees"] if r["user"].pk == self.employee.pk)
        own = usage_for(self.employee, self.employee, self.day, self.day)
        admin_page = usage_for(self.admin, self.employee, self.day, self.day)
        for key in ("app_seconds", "active_seconds", "idle_seconds", "activity_percent", "productive_percent"):
            self.assertEqual(row[key], admin_page[key], key)
            self.assertEqual(own[key], admin_page[key], key)

    def test_the_chart_matches_the_totals(self):
        trend = daily_trend(self.org, [self.employee.pk], self.day, self.day)
        app_hours = sum(sum(values) for values in trend["series"].values())
        self.assertAlmostEqual(app_hours * 3600, 120, places=0)
        self.assertAlmostEqual(trend["idle_hours"][0] * 3600, 30, places=0)
        self.assertEqual(trend["activity_percent"][0], 75.0)  # 90 active of 120 active+idle

    def test_a_range_is_the_sum_of_its_days(self):
        next_day = date(2026, 11, 3)
        app(self.device, ist(next_day, 9, 0, 0), "chrome.exe", seconds=45)
        activity(self.device, ist(next_day, 9, 0, 30), "active", seconds=30)
        rebuild_recent(since_hours=1)
        both = usage_for(self.admin, self.employee, self.day, next_day)
        first = usage_for(self.admin, self.employee, self.day, self.day)
        second = usage_for(self.admin, self.employee, next_day, next_day)
        self.assertEqual(both["app_seconds"], first["app_seconds"] + second["app_seconds"])
        self.assertEqual(both["active_seconds"], first["active_seconds"] + second["active_seconds"])
        self.assertEqual(both["app_seconds"], 165)
