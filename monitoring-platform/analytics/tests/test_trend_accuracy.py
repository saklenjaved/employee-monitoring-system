"""The Application time per day chart and table must agree with the raw events and with the totals on the page."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from analytics.models import ItemUsageDaily
from analytics.services import daily_trend, usage_for
from analytics.summaries import rebuild_day
from analytics.tests.test_employee_detail import at_local, today_in_display_zone
from analytics.tests.test_employee_live import add_sample
from devices.tests.test_setup import make_device
from monitoring.models import EventType, MonitoringEvent


class TrendAccuracyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org Trend")
        cls.emp = make_user("trend@t.test", Role.EMPLOYEE, cls.org, "Trend Employee")
        cls.device = make_device(cls.emp, last_seen=timezone.now())
        today = today_in_display_zone()
        cls.days = [today - timedelta(days=2), today - timedelta(days=1), today]
        for day_number, day in enumerate(cls.days):
            add_sample(cls.device, "chrome.exe", "active", at_local(day, 9, 0), seconds=120.0)
            add_sample(cls.device, "notepad.exe", "idle", at_local(day, 10, 0), seconds=45.0)
            rebuild_day(cls.emp, day)
        cls.start, cls.end = cls.days[0], cls.days[-1]

    def trend(self):
        return daily_trend(self.org, [self.emp.pk], self.start, self.end)

    def test_the_chart_has_one_value_per_day_in_the_range(self):
        data = self.trend()
        self.assertEqual(data["labels"], [day.isoformat() for day in self.days])
        for key in ("productive", "neutral", "unproductive", "unclassified"):
            self.assertEqual(len(data["series"][key]), 3)

    def test_the_chart_matches_the_raw_app_time_each_day(self):
        data = self.trend()
        chart_seconds = sum(sum(values) for values in data["series"].values()) * 3600
        raw_seconds = sum(
            float(event.payload.get("duration_seconds", 0))
            for event in MonitoringEvent.objects.filter(
                organization=self.org, user=self.emp, event_type=EventType.APP_FOCUS
            )
        )
        self.assertAlmostEqual(chart_seconds, raw_seconds, places=2)

    def test_the_chart_totals_equal_the_page_totals(self):
        data = self.trend()
        page = usage_for(self.emp, self.emp, self.start, self.end)
        for key, value in page["category_seconds"].items():
            chart_total = sum(data["series"][key]) * 3600
            self.assertAlmostEqual(chart_total, value, places=2, msg=key)
        self.assertAlmostEqual(sum(data["idle_hours"]) * 3600, page["idle_seconds"], places=2)

    def test_each_day_matches_the_daily_summary(self):
        data = self.trend()
        for index, day in enumerate(self.days):
            stored = sum(row.seconds for row in ItemUsageDaily.objects.filter(user=self.emp, date=day))
            chart = sum(data["series"][key][index] for key in data["series"]) * 3600
            self.assertAlmostEqual(chart, stored, places=2, msg=day.isoformat())
