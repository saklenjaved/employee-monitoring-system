"""The employee's own Audits tab. It shows only entries about the signed-in employee."""
import uuid
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from audit.services import record_event
from analytics.tests.test_employee_detail import today_in_display_zone
from devices.tests.test_setup import make_device
from screenshots.models import Screenshot, ScreenshotStatus


def make_shot(user, device):
    return Screenshot.objects.create(
        organization=user.organization,
        user=user,
        device=device,
        screenshot_id=uuid.uuid4(),
        object_key=f"shots/{uuid.uuid4().hex}.png",
        captured_at=timezone.now(),
        status=ScreenshotStatus.UPLOADED,
        byte_size=100,
        sha256="0" * 64,
    )


class MyAuditsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.admin = make_user("admin-a@t.test", Role.ADMIN, cls.org)
        cls.me = make_user("me-a@t.test", Role.EMPLOYEE, cls.org, "Me Audit")
        cls.other = make_user("other-a@t.test", Role.EMPLOYEE, cls.org, "Other Audit")
        cls.my_device = make_device(cls.me, last_seen=timezone.now())
        cls.other_device = make_device(cls.other, last_seen=timezone.now())
        cls.my_shot = make_shot(cls.me, cls.my_device)
        cls.other_shot = make_shot(cls.other, cls.other_device)

        record_event(cls.me, "user.password_changed", organization=cls.org, target=cls.me)
        record_event(cls.admin, "user.password_changed_in_admin", organization=cls.org, target=cls.me)
        record_event(cls.admin, "screenshot.viewed", organization=cls.org, target=cls.my_shot)
        record_event(cls.me, "screenshot.uploaded", organization=cls.org, target=cls.my_shot)
        record_event(cls.admin, "user.activated", organization=cls.org, target=cls.other)
        record_event(cls.admin, "screenshot.viewed", organization=cls.org, target=cls.other_shot)
        cls.today = today_in_display_zone()

    def setUp(self):
        self.client.force_login(self.me)

    def test_shows_only_actions_the_employee_did(self):
        html = self.client.get(reverse("my_audits")).content.decode()
        self.assertIn("user.password_changed", html)
        self.assertNotIn("user.password_changed_in_admin", html)
        self.assertNotIn("screenshot.viewed", html)
        self.assertNotIn("screenshot.uploaded", html)

    def test_does_not_show_entries_about_other_employees(self):
        html = self.client.get(reverse("my_audits")).content.decode()
        self.assertNotIn("user.activated", html)

    def test_admin_actions_are_not_shown_and_no_one_else_is_named(self):
        response = self.client.get(reverse("my_audits"))
        self.assertContains(response, "<td>You</td>")
        self.assertNotContains(response, "<td>Administrator</td>")
        self.assertNotContains(response, "admin-a@t.test")
        self.assertNotContains(response, "other-a@t.test")

    def test_the_date_filter_applies(self):
        yesterday = (self.today - timedelta(days=1)).isoformat()
        response = self.client.get(reverse("my_audits"), {"start": yesterday, "end": yesterday})
        self.assertContains(response, "No audit entries in these dates.")

    def test_admins_cannot_open_the_employee_audits_tab(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("my_audits")).status_code, 403)

    def test_the_sidebar_shows_audits_for_an_employee(self):
        html = self.client.get(reverse("my_audits")).content.decode()
        self.assertIn(f'href="{reverse("my_audits")}"', html)
        self.assertIn('aria-current="page" title="Audits"', html)
