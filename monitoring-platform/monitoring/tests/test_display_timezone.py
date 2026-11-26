"""Times on pages are shown in Indian Standard Time. Stored times stay in UTC."""
from datetime import datetime, timezone as dt_timezone
from zoneinfo import ZoneInfo

from django.template import Context, Template
from django.test import TestCase, override_settings

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from monitoring.services import zone_context

IST = ZoneInfo("Asia/Kolkata")


class DisplayTimezoneTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)

    def test_the_display_zone_is_indian_standard_time(self):
        context = zone_context(self.org)
        self.assertEqual(context["zone"], IST)
        self.assertEqual(context["zone_label"], "IST")

    def test_the_site_default_zone_is_indian_standard_time(self):
        from django.conf import settings

        self.assertEqual(settings.TIME_ZONE, "Asia/Kolkata")
        self.assertTrue(settings.USE_TZ)

    def test_a_utc_time_is_rendered_as_ist_on_a_page(self):
        # 06:43 UTC is 12:13 IST, the time a screenshot was uploaded in testing.
        stored = datetime(2026, 11, 26, 6, 43, 10, tzinfo=dt_timezone.utc)
        rendered = Template("{% load tz %}{{ value|date:'H:i' }}").render(Context({"value": stored}))
        self.assertEqual(rendered, "12:13")

    @override_settings(TIME_ZONE="Asia/Kolkata")
    def test_zone_context_time_matches_the_utc_instant(self):
        stored = datetime(2026, 11, 26, 6, 43, 10, tzinfo=dt_timezone.utc)
        local = stored.astimezone(zone_context(self.org)["zone"])
        self.assertEqual(local.strftime("%H:%M"), "12:13")
