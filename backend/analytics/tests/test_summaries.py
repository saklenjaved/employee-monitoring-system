from datetime import date, timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from analytics.models import ActivityDaily, ItemUsageDaily
from analytics.summaries import rebuild_day, rebuild_recent
from analytics.tests.helpers import activity, add_event, app, at, make_device, site
from monitoring.models import MonitoringEvent
from monitoring.services import update_policy

DAY = date(2026, 11, 2)


def usage(user, day=DAY):
    return {
        (row.item_type, row.name): row.seconds for row in ItemUsageDaily.objects.filter(user=user, date=day)
    }


class CountingTests(TestCase):
    """Every second is counted once, however the samples overlap."""

    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org")
        cls.user = make_user("e@org.test", Role.EMPLOYEE, cls.org)
        cls.other = make_user("o@org.test", Role.EMPLOYEE, cls.org)

    def setUp(self):
        self.pc = make_device(self.user, "PC-1")
        self.laptop = make_device(self.user, "PC-2")

    def test_consecutive_samples_add_up(self):
        for minute_second in (30, 60, 90, 120):
            app(self.pc, at(10, 0) + timedelta(seconds=minute_second))
        rebuild_day(self.user, DAY)
        self.assertEqual(usage(self.user), {("app", "chrome.exe"): 120})

    def test_different_applications_are_counted_separately(self):
        app(self.pc, at(10, 0, 30), "chrome.exe")
        app(self.pc, at(10, 1, 0), "code.exe")
        app(self.pc, at(10, 1, 30), "code.exe")
        rebuild_day(self.user, DAY)
        self.assertEqual(usage(self.user), {("app", "chrome.exe"): 30, ("app", "code.exe"): 60})

    def test_application_names_are_lower_cased(self):
        app(self.pc, at(10, 0, 30), "Chrome.EXE")
        rebuild_day(self.user, DAY)
        self.assertEqual(list(usage(self.user)), [("app", "chrome.exe")])

    def test_the_same_time_from_two_devices_is_counted_once(self):
        app(self.pc, at(10, 0, 30), "chrome.exe")
        app(self.laptop, at(10, 0, 30), "chrome.exe")
        rebuild_day(self.user, DAY)
        self.assertEqual(usage(self.user), {("app", "chrome.exe"): 30})

    def test_overlap_between_different_applications_goes_to_the_one_that_started_first(self):
        app(self.pc, at(10, 0, 30), "chrome.exe")  # 10:00:00 - 10:00:30
        app(self.laptop, at(10, 0, 45), "code.exe")  # 10:00:15 - 10:00:45
        rebuild_day(self.user, DAY)
        self.assertEqual(usage(self.user), {("app", "chrome.exe"): 30, ("app", "code.exe"): 15})

    def test_a_short_sample_inside_a_long_one_adds_nothing(self):
        app(self.pc, at(10, 1, 0), "chrome.exe", seconds=60)
        app(self.laptop, at(10, 0, 40), "code.exe", seconds=10)
        rebuild_day(self.user, DAY)
        self.assertEqual(usage(self.user), {("app", "chrome.exe"): 60})

    def test_gaps_are_not_counted(self):
        app(self.pc, at(10, 0, 30))
        app(self.pc, at(10, 5, 30))
        rebuild_day(self.user, DAY)
        self.assertEqual(usage(self.user), {("app", "chrome.exe"): 60})

    def test_website_time_is_kept_apart_from_application_time(self):
        app(self.pc, at(10, 1, 0), "chrome.exe", seconds=60)
        site(self.pc, at(10, 1, 0), "Example.com", seconds=60)
        rebuild_day(self.user, DAY)
        self.assertEqual(usage(self.user), {("app", "chrome.exe"): 60, ("website", "example.com"): 60})

    def test_website_visit_without_duration_is_ignored(self):
        add_event(self.pc, "website_visit", at(10, 0, 30), {"domain": "example.com"})
        rebuild_day(self.user, DAY)
        self.assertEqual(usage(self.user), {})

    def test_other_employees_events_are_not_mixed_in(self):
        app(self.pc, at(10, 0, 30))
        app(make_device(self.other), at(10, 0, 30), "code.exe")
        rebuild_day(self.user, DAY)
        self.assertEqual(usage(self.user), {("app", "chrome.exe"): 30})
        self.assertEqual(ItemUsageDaily.objects.filter(user=self.other).count(), 0)

    def test_rebuilding_twice_gives_the_same_result_and_picks_up_new_events(self):
        app(self.pc, at(10, 0, 30))
        rebuild_day(self.user, DAY)
        rebuild_day(self.user, DAY)
        self.assertEqual(usage(self.user), {("app", "chrome.exe"): 30})
        app(self.pc, at(10, 1, 0))
        rebuild_day(self.user, DAY)
        self.assertEqual(usage(self.user), {("app", "chrome.exe"): 60})
        self.assertEqual(ItemUsageDaily.objects.filter(user=self.user).count(), 1)

    def test_the_busiest_items_are_kept_and_the_rest_dropped(self):
        for k in range(305):
            app(self.pc, at(10, 0, 0) + timedelta(seconds=60 * k), f"app{k:03d}.exe", seconds=1 + k // 10)
        rebuild_day(self.user, DAY)
        names = {row.name for row in ItemUsageDaily.objects.filter(user=self.user)}
        self.assertEqual(len(names), 300)
        self.assertNotIn("app000.exe", names)  # among the shortest, so dropped
        self.assertIn("app304.exe", names)

    def test_events_filed_under_another_organization_are_not_counted(self):
        stray_org = make_org("Elsewhere")
        stray = app(self.pc, at(10, 0, 30))
        MonitoringEvent.objects.filter(pk=stray.pk).update(organization=stray_org)
        rebuild_day(self.user, DAY)
        self.assertFalse(ItemUsageDaily.objects.filter(user=self.user).exists())

    def test_a_day_without_events_has_no_rows(self):
        ItemUsageDaily.objects.create(organization=self.org, user=self.user, date=DAY, item_type="app", name="old.exe", seconds=5)
        rebuild_day(self.user, DAY)
        self.assertFalse(ItemUsageDaily.objects.filter(user=self.user).exists())
        self.assertFalse(ActivityDaily.objects.filter(user=self.user).exists())


class ActivityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org")
        cls.user = make_user("e@org.test", Role.EMPLOYEE, cls.org)

    def setUp(self):
        self.pc = make_device(self.user, "PC-1")
        self.laptop = make_device(self.user, "PC-2")

    def totals(self):
        row = ActivityDaily.objects.get(user=self.user, date=DAY)
        return row.active_seconds, row.idle_seconds

    def test_active_and_idle_seconds(self):
        activity(self.pc, at(10, 0, 30), "active")
        activity(self.pc, at(10, 1, 0), "active")
        activity(self.pc, at(10, 1, 30), "idle")
        rebuild_day(self.user, DAY)
        self.assertEqual(self.totals(), (60, 30))

    def test_active_wins_when_two_devices_disagree(self):
        activity(self.pc, at(10, 1, 0), "active", seconds=60)  # 10:00 - 10:01 active
        activity(self.laptop, at(10, 2, 0), "idle", seconds=120)  # 10:00 - 10:02 idle
        rebuild_day(self.user, DAY)
        self.assertEqual(self.totals(), (60, 60))

    def test_the_same_sample_from_two_devices_is_counted_once(self):
        activity(self.pc, at(10, 0, 30), "idle")
        activity(self.laptop, at(10, 0, 30), "idle")
        rebuild_day(self.user, DAY)
        self.assertEqual(self.totals(), (0, 30))


class TimeZoneTests(TestCase):
    """A day is a calendar day in the organization's time zone."""

    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org")
        cls.admin = make_user("admin@org.test", Role.ADMIN, cls.org)
        cls.user = make_user("e@org.test", Role.EMPLOYEE, cls.org)
        update_policy(cls.admin, cls.org, {"timezone": "Asia/Kolkata"})  # UTC+5:30

    def test_time_is_split_at_local_midnight(self):
        pc = make_device(self.user)
        # Local midnight on 2 Nov is 2026-11-01 18:30 UTC. This sample covers 18:29:30 - 18:30:30 UTC.
        app(pc, at(18, 30, 30, day=1), "chrome.exe", seconds=60)
        rebuild_day(self.user, date(2026, 11, 1))
        rebuild_day(self.user, date(2026, 11, 2))
        self.assertEqual(usage(self.user, date(2026, 11, 1)), {("app", "chrome.exe"): 30})
        self.assertEqual(usage(self.user, date(2026, 11, 2)), {("app", "chrome.exe"): 30})

    def test_a_utc_evening_belongs_to_the_next_local_day(self):
        pc = make_device(self.user)
        app(pc, at(20, 0, 30, day=1), "chrome.exe")  # 01:30 local on 2 Nov
        rebuild_day(self.user, date(2026, 11, 1))
        rebuild_day(self.user, date(2026, 11, 2))
        self.assertEqual(usage(self.user, date(2026, 11, 1)), {})
        self.assertEqual(usage(self.user, date(2026, 11, 2)), {("app", "chrome.exe"): 30})


class RebuildRecentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org")
        cls.user = make_user("e@org.test", Role.EMPLOYEE, cls.org)

    def test_late_events_rebuild_the_old_day(self):
        pc = make_device(self.user)
        app(pc, at(10, 0, 30, day=1))  # captured yesterday, received just now (offline queue)
        count = rebuild_recent(since_hours=1)
        self.assertGreaterEqual(count, 1)
        self.assertEqual(usage(self.user, date(2026, 11, 1)), {("app", "chrome.exe"): 30})

    def test_events_received_long_ago_are_left_alone(self):
        pc = make_device(self.user)
        old = app(pc, at(10, 0, 30, day=1))
        MonitoringEvent.objects.filter(pk=old.pk).update(received_at=timezone.now() - timedelta(days=10))
        self.assertEqual(rebuild_recent(since_hours=72), 0)
        self.assertFalse(ItemUsageDaily.objects.exists())

    def test_events_just_after_midnight_also_rebuild_the_previous_day(self):
        pc = make_device(self.user)
        app(pc, at(0, 0, 20, day=2), "chrome.exe", seconds=30)  # covers 23:59:50 - 00:00:20
        rebuild_recent(since_hours=1)
        self.assertEqual(usage(self.user, date(2026, 11, 1)), {("app", "chrome.exe"): 10})
        self.assertEqual(usage(self.user, date(2026, 11, 2)), {("app", "chrome.exe"): 20})

    def test_a_long_sample_after_midnight_gives_time_to_the_day_before(self):
        pc = make_device(self.user)
        app(pc, at(0, 30, 0, day=2), "chrome.exe", seconds=3600)  # covers 23:30 - 00:30
        rebuild_recent(since_hours=1)
        self.assertEqual(usage(self.user, date(2026, 11, 1)), {("app", "chrome.exe"): 1800})
        self.assertEqual(usage(self.user, date(2026, 11, 2)), {("app", "chrome.exe"): 1800})

    def test_rebuild_recent_loads_all_employees_in_one_query(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        users = [make_user(f"u{i}@org.test", Role.EMPLOYEE, self.org) for i in range(6)]
        for user in users:
            app(make_device(user), at(10, 0, 30), "chrome.exe")
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(rebuild_recent(since_hours=1), 6)  # one day for each of the six employees
        lookups = [q["sql"] for q in queries if 'FROM "accounts_user"' in q["sql"]]
        self.assertEqual(len(lookups), 1)

    def test_management_command(self):
        app(make_device(self.user), at(10, 0, 30))
        output = StringIO()
        call_command("build_summaries", "--since-hours", "1", stdout=output)
        self.assertIn("Rebuilt", output.getvalue())
        self.assertTrue(ItemUsageDaily.objects.filter(user=self.user).exists())
