"""The gallery's search and sort work on real records, and never show another organization's people."""
from datetime import datetime, timedelta, timezone

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from analytics.tests.helpers import make_device
from screenshots import services
from screenshots.tests.test_pages import SETTINGS, make_shot

UTC = timezone.utc


class GalleryFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.erin = make_user("erin@a.test", Role.EMPLOYEE, cls.org_a, "Erin One")
        cls.eli = make_user("eli@a.test", Role.EMPLOYEE, cls.org_a, "Eli Two")
        cls.bea = make_user("bea@b.test", Role.EMPLOYEE, cls.org_b, "Bea Secret")
        cls.d_erin = make_device(cls.erin, "PC-ERIN")
        cls.d_eli = make_device(cls.eli, "PC-ELI")
        cls.d_bea = make_device(cls.bea, "PC-BEA")
        cls.early = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
        cls.erin_late = make_shot(cls.erin, cls.d_erin, cls.early + timedelta(hours=5))
        cls.erin_early = make_shot(cls.erin, cls.d_erin, cls.early)
        cls.eli_mid = make_shot(cls.eli, cls.d_eli, cls.early + timedelta(hours=2))
        cls.bea_shot = make_shot(cls.bea, cls.d_bea, cls.early)
        services.update_policy(cls.admin_a, cls.org_a, SETTINGS)

    def _page(self, params):
        self.client.force_login(self.admin_a)
        response = self.client.get(reverse("screenshot_list"), params)
        self.assertEqual(response.status_code, 200)
        return list(response.context["page"].object_list)

    def test_search_by_name_returns_only_that_employees_screenshots(self):
        shots = self._page({"q": "Erin"})
        self.assertEqual({shot.pk for shot in shots}, {self.erin_late.pk, self.erin_early.pk})

    def test_newest_first_is_the_default_and_oldest_first_reverses_it(self):
        newest = [shot.pk for shot in self._page({})]
        self.assertEqual(newest[0], self.erin_late.pk)
        oldest = [shot.pk for shot in self._page({"order": "oldest"})]
        self.assertEqual(oldest, list(reversed(newest)))

    def test_times_are_shown_in_indian_time(self):
        # Eli's screenshot is at 11:00 UTC, which is 16:30 IST (UTC+5:30).
        self.client.force_login(self.admin_a)
        response = self.client.get(reverse("screenshot_list"), {"q": "Eli"})
        self.assertContains(response, "2 Nov 2026, 16:30 IST")
        self.assertNotContains(response, "UTC")

    def test_search_never_shows_another_organizations_employee(self):
        self.assertEqual(self._page({"q": "Bea"}), [])
