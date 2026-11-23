"""An employee's profile shows only that employee's monitoring data, and only to people allowed to see it."""
import uuid
from datetime import datetime, timezone

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from analytics.tests.helpers import make_device
from analytics.tests.test_dashboards import activity, usage
from screenshots.models import Screenshot, ScreenshotPolicy, ScreenshotStatus
from workforce.models import Team, UserTeam

CAPTURED = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def shot(user, device, minute):
    return Screenshot.objects.create(
        organization=user.organization,
        user=user,
        device=device,
        screenshot_id=uuid.uuid4(),
        object_key=f"shots/{uuid.uuid4().hex}.png",
        captured_at=CAPTURED.replace(minute=minute),
        status=ScreenshotStatus.UPLOADED,
        byte_size=100,
        sha256="0" * 64,
    )


class EmployeeProfileIsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.other_org = make_org("Org B")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)
        cls.other_admin = make_user("admin@b.test", Role.ADMIN, cls.other_org)
        cls.alice = make_user("alice@a.test", Role.EMPLOYEE, cls.org, "Alice Team")
        cls.bob = make_user("bob@a.test", Role.EMPLOYEE, cls.org, "Bob Other")
        cls.stranger = make_user("x@b.test", Role.EMPLOYEE, cls.other_org, "Xena Outside")
        cls.manager = make_user("mgr@a.test", Role.MANAGER, cls.org, "Manager")

        team = Team.objects.create(organization=cls.org, name="Support")
        UserTeam.objects.create(organization=cls.org, team=team, user=cls.manager)
        UserTeam.objects.create(organization=cls.org, team=team, user=cls.alice)

        usage(cls.alice, "alice-only.exe", 60)
        usage(cls.alice, "alice-site.example", 30, "website")
        activity(cls.alice, 90, 30)
        usage(cls.bob, "bob-only.exe", 500)
        activity(cls.bob, 10, 800)
        usage(cls.stranger, "stranger-only.exe", 900)

        cls.alice_shot = shot(cls.alice, make_device(cls.alice, "PC-A"), 5)
        cls.bob_shot = shot(cls.bob, make_device(cls.bob, "PC-B"), 6)

    def profile(self, user, employee):
        self.client.force_login(user)
        return self.client.get(reverse("dashboard_employee", args=[employee.pk]), {"start": "2026-11-02", "end": "2026-11-02"})

    def test_profile_shows_only_that_employees_usage(self):
        page = self.profile(self.admin, self.alice).content.decode()
        self.assertIn("alice-only.exe", page)
        self.assertIn("alice-site.example", page)
        self.assertNotIn("bob-only.exe", page)
        self.assertNotIn("stranger-only.exe", page)

    def test_profile_shows_only_that_employees_screenshots(self):
        page = self.profile(self.admin, self.alice).content.decode()
        self.assertIn(reverse("screenshot_detail", args=[self.alice_shot.pk]), page)
        self.assertNotIn(reverse("screenshot_detail", args=[self.bob_shot.pk]), page)

    def test_profile_links_to_that_employees_screenshot_list(self):
        page = self.profile(self.admin, self.alice).content.decode()
        self.assertIn(f"{reverse('screenshot_list')}?employee={self.alice.pk}", page)

    def test_profile_shows_monitoring_hours(self):
        page = self.profile(self.admin, self.alice).content.decode()
        self.assertIn("Working hours", page)
        self.assertIn("Monitored hours", page)

    def test_admin_of_another_organization_cannot_open_the_profile(self):
        self.assertEqual(self.profile(self.other_admin, self.alice).status_code, 404)

    def test_manager_cannot_open_an_employee_outside_their_team(self):
        self.assertEqual(self.profile(self.manager, self.bob).status_code, 404)

    def test_manager_sees_no_screenshots_unless_the_organization_allows_it(self):
        response = self.profile(self.manager, self.alice)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(reverse("screenshot_detail", args=[self.alice_shot.pk]), response.content.decode())

    def test_manager_sees_their_team_member_screenshots_when_allowed(self):
        ScreenshotPolicy.objects.create(organization=self.org, managers_can_view=True)
        response = self.profile(self.manager, self.alice)
        self.assertIn(reverse("screenshot_detail", args=[self.alice_shot.pk]), response.content.decode())
        self.assertNotIn(reverse("screenshot_detail", args=[self.bob_shot.pk]), response.content.decode())

    def test_live_panel_only_lists_that_employees_activity(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard_employee_live", args=[self.alice.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("bob-only.exe", response.content.decode())
