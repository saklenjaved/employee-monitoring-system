"""Quick ranges on the dashboards: Today and Last 7 days."""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from analytics.dashboards import _today_for


class PresetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.emp = make_user("e@a.test", Role.EMPLOYEE, cls.org, "Employee A")

    def dashboard(self, query=""):
        self.client.force_login(self.emp)
        return self.client.get(reverse("dashboard_me") + query)

    def test_today_shows_one_day(self):
        response = self.dashboard("?preset=day")
        self.assertEqual(response.context["start"], response.context["end"])
        self.assertEqual(response.context["preset"], "day")
        self.assertContains(response, 'class="preset active"', count=1)

    def test_last_7_days_covers_seven_days_ending_today(self):
        response = self.dashboard("?preset=week")
        start, end = response.context["start"], response.context["end"]
        self.assertEqual(end - start, timedelta(days=6))
        self.assertEqual(end, _today_for(self.org))
        self.assertContains(response, "Last 7 days")

    def test_an_unknown_preset_is_ignored(self):
        response = self.dashboard("?preset=year")
        self.assertEqual(response.context["start"], response.context["end"])

    def test_the_manual_range_still_works_without_a_preset(self):
        response = self.dashboard("?start=2026-11-01&end=2026-11-03")
        self.assertEqual(str(response.context["start"]), "2026-11-01")
        self.assertEqual(str(response.context["end"]), "2026-11-03")
