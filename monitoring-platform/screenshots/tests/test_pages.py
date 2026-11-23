import uuid
from datetime import datetime, timedelta, timezone

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import make_org, make_super_admin, make_user
from analytics.tests.helpers import make_device
from audit.models import AuditLog
from screenshots import services
from screenshots.models import Screenshot, ScreenshotExclusion, ScreenshotPolicy, ScreenshotStatus
from screenshots.tests.fakes import FakeStorage
from workforce.models import Department, EmployeeProfile, Team, UserTeam

UTC = timezone.utc
SETTINGS = {"enabled": True, "interval_seconds": 600, "retention_days": 30, "blur_level": "low"}


def make_shot(user, device, captured_at, status=ScreenshotStatus.UPLOADED, error_code=""):
    return Screenshot.objects.create(
        organization=user.organization,
        user=user,
        device=device,
        screenshot_id=uuid.uuid4(),
        object_key=f"org-{user.organization_id}/test/{uuid.uuid4().hex}.png",
        captured_at=captured_at,
        status=status,
        error_code=error_code,
        byte_size=1000,
        sha256="a" * 64,
    )


class ScreenshotPageTestCase(TestCase):
    """Organization A: two teams, each with a Manager and an employee, plus one employee in no team."""

    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.admin_b = make_user("admin@b.test", Role.ADMIN, cls.org_b)
        cls.manager1 = make_user("m1@a.test", Role.MANAGER, cls.org_a, "Manager One")
        cls.manager2 = make_user("m2@a.test", Role.MANAGER, cls.org_a, "Manager Two")
        cls.e1 = make_user("e1@a.test", Role.EMPLOYEE, cls.org_a, "Erin One")
        cls.e2 = make_user("e2@a.test", Role.EMPLOYEE, cls.org_a, "Eli Two")
        cls.e3 = make_user("e3@a.test", Role.EMPLOYEE, cls.org_a, "Eve Three")
        cls.e_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b, "Bea Secret")
        cls.super_admin = make_super_admin()
        cls.team1 = Team.objects.create(organization=cls.org_a, name="Support")
        cls.team2 = Team.objects.create(organization=cls.org_a, name="Sales")
        for team, users in ((cls.team1, (cls.manager1, cls.e1)), (cls.team2, (cls.manager2, cls.e2))):
            for user in users:
                UserTeam.objects.create(organization=cls.org_a, team=team, user=user)
        cls.dept = Department.objects.create(organization=cls.org_a, name="Care")
        EmployeeProfile.objects.create(user=cls.e1, organization=cls.org_a, department=cls.dept, employee_code="E-100")

        cls.d1, cls.d2, cls.d3, cls.d_b = (make_device(u, f"PC-{i}") for i, u in enumerate((cls.e1, cls.e2, cls.e3, cls.e_b)))
        cls.day = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
        cls.shot1 = make_shot(cls.e1, cls.d1, cls.day)
        cls.shot1_old = make_shot(cls.e1, cls.d1, cls.day - timedelta(days=3))
        cls.shot2 = make_shot(cls.e2, cls.d2, cls.day + timedelta(minutes=5))
        cls.shot3 = make_shot(cls.e3, cls.d3, cls.day + timedelta(minutes=10))
        cls.failed = make_shot(cls.e1, cls.d1, cls.day + timedelta(minutes=20), ScreenshotStatus.FAILED, "upload_rejected")
        cls.pending = make_shot(cls.e1, cls.d1, cls.day + timedelta(minutes=30), ScreenshotStatus.AUTHORIZED)
        cls.shot_b = make_shot(cls.e_b, cls.d_b, cls.day)
        services.update_policy(cls.admin_a, cls.org_a, SETTINGS)
        services.update_policy(cls.admin_b, cls.org_b, SETTINGS)

    def setUp(self):
        FakeStorage.reset()

    def listed(self, user, **query):
        self.client.force_login(user)
        response = self.client.get(reverse("screenshot_list"), query)
        self.assertEqual(response.status_code, 200)
        return {shot.pk for shot in response.context["page"]}


class VisibilityTests(ScreenshotPageTestCase):
    def test_who_sees_which_screenshots(self):
        every_a = {self.shot1.pk, self.shot1_old.pk, self.shot2.pk, self.shot3.pk, self.failed.pk, self.pending.pk}
        self.assertEqual(self.listed(self.admin_a), every_a)
        self.assertEqual(self.listed(self.admin_b), {self.shot_b.pk})
        self.assertEqual(
            self.listed(self.manager1), {self.shot1.pk, self.shot1_old.pk, self.failed.pk, self.pending.pk}
        )
        self.assertEqual(self.listed(self.manager2), {self.shot2.pk})

    def test_a_manager_sees_nothing_when_the_organization_turns_manager_viewing_off(self):
        services.update_policy(self.admin_a, self.org_a, {"managers_can_view": False, "managers_can_download": False})
        self.assertEqual(self.listed(self.manager1), set())
        self.client.force_login(self.manager1)
        self.assertEqual(self.client.get(reverse("screenshot_detail", args=[self.shot1.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("screenshot_image", args=[self.shot1.pk])).status_code, 404)

    def test_a_manager_filter_lists_no_names_when_manager_viewing_is_off(self):
        self.client.force_login(self.manager1)
        form = self.client.get(reverse("screenshot_list")).context["form"]
        self.assertIn(self.e1, form.fields["employee"].queryset)
        services.update_policy(self.admin_a, self.org_a, {"managers_can_view": False})
        form = self.client.get(reverse("screenshot_list")).context["form"]
        for field in ("employee", "team", "department"):
            with self.subTest(field=field):
                self.assertFalse(form.fields[field].queryset.exists())

    def test_employees_the_super_admin_and_anonymous_users_are_kept_out(self):
        urls = [
            reverse("screenshot_list"),
            reverse("screenshot_detail", args=[self.shot1.pk]),
            reverse("screenshot_image", args=[self.shot1.pk]),
            reverse("screenshot_download", args=[self.shot1.pk]),
        ]
        for user in (self.e1, self.super_admin):
            self.client.force_login(user)
            for url in urls:
                with self.subTest(role=user.role, url=url):
                    self.assertEqual(self.client.get(url).status_code, 403)
        self.client.logout()
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)
        self.assertFalse(AuditLog.objects.filter(action__in=["screenshot.viewed", "screenshot.downloaded"]).exists())

    def test_another_organizations_screenshot_is_not_found(self):
        self.client.force_login(self.admin_a)
        for name in ("screenshot_detail", "screenshot_image", "screenshot_download"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name, args=[self.shot_b.pk])).status_code, 404)
        self.assertFalse(AuditLog.objects.filter(action__startswith="screenshot.v").exists())

    def test_a_manager_cannot_open_another_teams_screenshot(self):
        self.client.force_login(self.manager1)
        for name in ("screenshot_detail", "screenshot_image", "screenshot_download"):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name, args=[self.shot2.pk])).status_code, 404)
                self.assertEqual(self.client.get(reverse(name, args=[self.shot3.pk])).status_code, 404)
        self.assertFalse(AuditLog.objects.filter(action__startswith="screenshot.v").exists())

    def test_list_shows_employee_name_id_device_and_status(self):
        self.client.force_login(self.admin_a)
        page = self.client.get(reverse("screenshot_list"))
        for text in ("Erin One", "E-100", "PC-0", "Uploaded", "Failed", "upload_rejected", "Waiting for upload"):
            self.assertContains(page, text)
        self.assertNotContains(page, "Bea Secret")


class FilterTests(ScreenshotPageTestCase):
    def test_filters(self):
        self.assertEqual(self.listed(self.admin_a, employee=self.e2.pk), {self.shot2.pk})
        self.assertEqual(
            self.listed(self.admin_a, team=self.team1.pk),
            {self.shot1.pk, self.shot1_old.pk, self.failed.pk, self.pending.pk},
        )
        self.assertEqual(
            self.listed(self.admin_a, department=self.dept.pk),
            {self.shot1.pk, self.shot1_old.pk, self.failed.pk, self.pending.pk},
        )
        self.assertEqual(self.listed(self.admin_a, status="failed"), {self.failed.pk})
        self.assertEqual(
            self.listed(self.admin_a, start="2026-11-02", end="2026-11-02"),
            {self.shot1.pk, self.shot2.pk, self.shot3.pk, self.failed.pk, self.pending.pk},
        )
        self.assertEqual(self.listed(self.admin_a, start="2026-10-30", end="2026-10-30"), {self.shot1_old.pk})

    def test_filters_never_widen_what_a_manager_may_see(self):
        # An employee outside the Manager's teams is not a valid choice, so the filter is ignored.
        shown = self.listed(self.manager1, employee=self.e2.pk)
        self.assertNotIn(self.shot2.pk, shown)
        self.assertEqual(self.listed(self.manager1, team=self.team2.pk) & {self.shot2.pk}, set())

    def test_a_date_range_follows_the_organization_time_zone(self):
        from monitoring.services import update_policy as update_monitoring

        update_monitoring(self.admin_a, self.org_a, {"timezone": "Asia/Kolkata"})
        late = make_shot(self.e3, self.d3, datetime(2026, 11, 2, 20, 0, tzinfo=UTC))  # 3 Nov, 01:30 in India
        self.assertIn(late.pk, self.listed(self.admin_a, start="2026-11-03", end="2026-11-03"))
        self.assertNotIn(late.pk, self.listed(self.admin_a, start="2026-11-02", end="2026-11-02"))

    def test_the_list_is_paged_and_newest_first(self):
        for i in range(30):
            make_shot(self.e3, self.d3, datetime(2026, 12, 1, 8, 0, tzinfo=UTC) + timedelta(minutes=i))
        self.client.force_login(self.admin_a)
        first = self.client.get(reverse("screenshot_list"))
        self.assertEqual(len(first.context["page"]), 24)
        times = [shot.captured_at for shot in first.context["page"]]
        self.assertEqual(times, sorted(times, reverse=True))
        second = self.client.get(reverse("screenshot_list"), {"page": 2})
        self.assertGreater(len(second.context["page"]), 0)


class ViewAndDownloadTests(ScreenshotPageTestCase):
    def test_looking_at_an_image_redirects_to_a_short_lived_link_and_is_audited(self):
        self.client.force_login(self.admin_a)
        response = self.client.get(reverse("screenshot_image", args=[self.shot1.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"https://storage.test/get/{self.shot1.object_key}?expires=60")
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(response.content, b"")  # the image never passes through Django
        entry = AuditLog.objects.get(action="screenshot.viewed")
        self.assertEqual((entry.actor, entry.organization, entry.target_id), (self.admin_a, self.org_a, str(self.shot1.pk)))
        self.client.get(reverse("screenshot_image", args=[self.shot1.pk]))
        self.assertEqual(AuditLog.objects.filter(action="screenshot.viewed").count(), 2)  # every look counts

    def test_the_audit_note_holds_no_personal_data(self):
        self.client.force_login(self.admin_a)
        self.client.get(reverse("screenshot_image", args=[self.shot1.pk]))
        entry = AuditLog.objects.get(action="screenshot.viewed")
        self.assertEqual(entry.detail, "")

    def test_a_manager_may_view_their_teams_screenshot_but_not_download_by_default(self):
        self.client.force_login(self.manager1)
        self.assertEqual(self.client.get(reverse("screenshot_image", args=[self.shot1.pk])).status_code, 302)
        detail = self.client.get(reverse("screenshot_detail", args=[self.shot1.pk]))
        self.assertContains(detail, "do not have permission to download")
        self.assertNotContains(detail, reverse("screenshot_download", args=[self.shot1.pk]))
        self.assertEqual(self.client.get(reverse("screenshot_download", args=[self.shot1.pk])).status_code, 404)
        self.assertFalse(AuditLog.objects.filter(action="screenshot.downloaded").exists())

    def test_download_needs_explicit_permission_and_is_audited(self):
        services.update_policy(self.admin_a, self.org_a, {"managers_can_download": True})
        self.client.force_login(self.manager1)
        response = self.client.get(reverse("screenshot_download", args=[self.shot1.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("&download=screenshot-", response.url)
        self.assertNotIn("Erin", response.url)  # the file name holds no personal data
        self.assertNotIn("e1@a.test", response.url)
        entry = AuditLog.objects.get(action="screenshot.downloaded")
        self.assertEqual((entry.actor, entry.target_id), (self.manager1, str(self.shot1.pk)))

    def test_an_admin_may_always_download(self):
        self.client.force_login(self.admin_a)
        self.assertEqual(self.client.get(reverse("screenshot_download", args=[self.shot1.pk])).status_code, 302)

    def test_only_uploaded_screenshots_have_an_image(self):
        self.client.force_login(self.admin_a)
        for shot in (self.failed, self.pending):
            for name in ("screenshot_image", "screenshot_download"):
                with self.subTest(status=shot.status, page=name):
                    self.assertEqual(self.client.get(reverse(name, args=[shot.pk])).status_code, 404)
        self.assertFalse(AuditLog.objects.filter(action__startswith="screenshot.v").exists())
        detail = self.client.get(reverse("screenshot_detail", args=[self.failed.pk]))
        self.assertContains(detail, "has no image")
        self.assertNotContains(detail, "<img")

    def test_the_detail_page_points_to_the_audited_image_address(self):
        self.client.force_login(self.admin_a)
        detail = self.client.get(reverse("screenshot_detail", args=[self.shot1.pk]))
        self.assertContains(detail, reverse("screenshot_image", args=[self.shot1.pk]))
        self.assertNotContains(detail, "storage.test")  # signed links are never put into a page


class SettingsPageTests(ScreenshotPageTestCase):
    url = None

    def setUp(self):
        super().setUp()
        self.url = reverse("screenshot_settings")
        self.client.force_login(self.admin_a)

    def post(self, **overrides):
        data = {"enabled": "on", "interval_seconds": "900", "retention_days": "60", "blur_level": "high",
                "monitoring_minutes": "480", "managers_can_view": "on"}
        data.update(overrides)
        return self.client.post(self.url, {k: v for k, v in data.items() if v is not None})

    def test_saving_changes_the_policy_bumps_the_version_and_is_audited(self):
        before = ScreenshotPolicy.objects.get(organization=self.org_a).version
        self.assertRedirects(self.post(), self.url)
        policy = ScreenshotPolicy.objects.get(organization=self.org_a)
        self.assertEqual((policy.interval_seconds, policy.retention_days, policy.blur_level), (900, 60, "high"))
        self.assertEqual(policy.version, before + 1)
        entry = AuditLog.objects.filter(action="screenshot_policy.changed").latest("id")
        self.assertIn("interval_seconds: 600->900", entry.detail)
        self.assertIn("retention_days: 30->60", entry.detail)

    def test_screenshots_cannot_be_switched_on_until_everything_is_chosen(self):
        ScreenshotPolicy.objects.filter(organization=self.org_a).update(
            enabled=False, interval_seconds=None, retention_days=None, blur_level=""
        )
        for missing in ({"interval_seconds": ""}, {"retention_days": ""}, {"blur_level": ""}):
            with self.subTest(missing=missing):
                self.assertEqual(self.post(**missing).status_code, 200)
                self.assertFalse(ScreenshotPolicy.objects.get(organization=self.org_a).enabled)
        # Saving with the switch off and nothing chosen is fine.
        self.assertRedirects(self.post(enabled=None, interval_seconds="", retention_days="", blur_level=""), self.url)

    def test_values_are_limited(self):
        for overrides in ({"interval_seconds": "59"}, {"interval_seconds": "3601"}, {"retention_days": "0"},
                          {"retention_days": "99999"}, {"blur_level": "extreme"}):
            with self.subTest(overrides=overrides):
                self.assertEqual(self.post(**overrides).status_code, 200)
        self.assertEqual(ScreenshotPolicy.objects.get(organization=self.org_a).interval_seconds, 600)

    def test_download_for_managers_needs_viewing(self):
        self.assertEqual(self.post(managers_can_view=None, managers_can_download="on").status_code, 200)
        self.assertFalse(ScreenshotPolicy.objects.get(organization=self.org_a).managers_can_download)

    def test_saving_the_same_values_changes_nothing(self):
        policy = ScreenshotPolicy.objects.get(organization=self.org_a)
        self.post(interval_seconds="600", retention_days="30", blur_level="low")
        self.assertEqual(ScreenshotPolicy.objects.get(organization=self.org_a).version, policy.version)

    def status_of(self, name):
        rows = {row["name"]: row for row in self.client.get(self.url).context["device_rows"]}
        return rows[name]

    def test_each_pc_shows_whether_its_agent_has_the_current_settings(self):
        from datetime import timedelta as td
        from devices.services import agent_policy
        from django.utils import timezone as tz

        now = tz.now()
        self.d1.last_seen_at = now
        self.d1.policy_version_served = agent_policy(self.d1)["policy_version"]
        self.d1.save()  # connected and has the current settings
        self.d2.last_seen_at = None
        self.d2.save()  # never connected
        self.d3.last_seen_at = now
        self.d3.policy_version_served = "old"
        self.d3.save()  # connected but has old settings
        self.assertEqual(self.status_of("Erin One")["state"], "Screenshot monitoring active")
        self.assertTrue(self.status_of("Erin One")["applied"])
        self.assertEqual(self.status_of("Eli Two")["state"], "Waiting for agent connection")
        self.assertEqual(self.status_of("Eve Three")["state"], "Waiting for agent to apply settings")
        self.assertEqual(self.status_of("Erin One")["interval"], 600)
        self.assertEqual(self.status_of("Erin One")["last"].pk, self.pending.pk)  # the newest by capture time
        self.assertIsNotNone(self.status_of("Erin One")["last_when"])
        # Turning the setting off changes every PC's state.
        services.update_policy(self.admin_a, self.org_a, {"enabled": False})
        self.assertEqual(self.status_of("Erin One")["state"], "Monitoring disabled")

    def test_settings_table_is_limited_to_the_organization(self):
        response = self.client.get(self.url)
        names = {row["name"] for row in response.context["device_rows"]}
        self.assertNotIn("Bea Secret", names)

    def test_only_admins_change_settings_and_only_for_their_own_organization(self):
        for user in (self.manager1, self.e1, self.super_admin):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertEqual(self.client.post(self.url, {"enabled": "on"}).status_code, 403)
        self.client.force_login(self.admin_a)
        self.post(interval_seconds="1200")
        self.assertEqual(ScreenshotPolicy.objects.get(organization=self.org_b).interval_seconds, 600)


class DeleteScreenshotTests(ScreenshotPageTestCase):
    def delete(self, user, shot):
        self.client.force_login(user)
        return self.client.post(reverse("screenshot_delete", args=[shot.pk]))

    def test_an_admin_deletes_a_screenshot_of_their_organization_and_its_image(self):
        shot_key = self.shot2.object_key
        response = self.delete(self.admin_a, self.shot2)
        self.assertRedirects(response, reverse("screenshot_list"))
        self.assertFalse(Screenshot.objects.filter(pk=self.shot2.pk).exists())
        self.assertIn(shot_key, FakeStorage.deleted)
        entry = AuditLog.objects.filter(action="screenshot.deleted").latest("id")
        self.assertEqual(entry.actor_id, self.admin_a.pk)
        self.assertEqual(entry.organization_id, self.org_a.pk)
        self.assertEqual(entry.target_id, str(self.shot2.pk))
        self.assertNotIn("@", entry.detail)  # no names or emails in the record

    def test_managers_and_employees_cannot_delete(self):
        for user in (self.manager1, self.e1):
            with self.subTest(user=user.email):
                self.assertEqual(self.delete(user, self.shot1).status_code, 403)
                self.assertTrue(Screenshot.objects.filter(pk=self.shot1.pk).exists())

    def test_an_admin_of_another_organization_cannot_delete(self):
        self.assertEqual(self.delete(self.admin_b, self.shot2).status_code, 404)
        self.assertTrue(Screenshot.objects.filter(pk=self.shot2.pk).exists())
        self.assertFalse(AuditLog.objects.filter(action="screenshot.deleted").exists())

    def test_deleting_needs_a_post(self):
        self.client.force_login(self.admin_a)
        self.assertEqual(self.client.get(reverse("screenshot_delete", args=[self.shot2.pk])).status_code, 405)
        self.assertTrue(Screenshot.objects.filter(pk=self.shot2.pk).exists())

    def test_the_detail_page_shows_delete_only_to_admins(self):
        self.client.force_login(self.admin_a)
        self.assertContains(self.client.get(reverse("screenshot_detail", args=[self.shot2.pk])), "Delete screenshot")
        self.client.force_login(self.manager1)
        self.assertNotContains(self.client.get(reverse("screenshot_detail", args=[self.shot1.pk])), "Delete screenshot")


class ExclusionPageTests(ScreenshotPageTestCase):
    def setUp(self):
        super().setUp()
        self.url = reverse("screenshot_exclusions")
        self.client.force_login(self.admin_a)

    def test_add_and_remove_with_audit(self):
        self.assertRedirects(self.client.post(self.url, {"team": self.team1.pk}), self.url)
        self.client.post(self.url, {"employee": self.e3.pk})
        self.assertEqual(ScreenshotExclusion.objects.count(), 2)
        self.assertEqual(AuditLog.objects.filter(action="screenshot_exclusion.added").count(), 2)
        exclusion = ScreenshotExclusion.objects.get(user=self.e3)
        self.client.post(reverse("screenshot_exclusion_remove", args=[exclusion.pk]))
        self.assertFalse(ScreenshotExclusion.objects.filter(user=self.e3).exists())
        self.assertTrue(AuditLog.objects.filter(action="screenshot_exclusion.removed").exists())

    def test_exclusions_change_what_the_agent_is_told(self):
        before = ScreenshotPolicy.objects.get(organization=self.org_a).version
        self.client.post(self.url, {"employee": self.e3.pk})
        self.assertEqual(ScreenshotPolicy.objects.get(organization=self.org_a).version, before + 1)
        self.assertEqual(services.agent_screenshot_policy(self.d3), {"enabled": False})
        self.assertTrue(services.agent_screenshot_policy(self.d1)["enabled"])

    def test_bad_choices_are_refused(self):
        for data in ({}, {"team": self.team1.pk, "employee": self.e3.pk}, {"team": self.team1.pk + 999},
                     {"employee": self.manager1.pk}):
            with self.subTest(data=data):
                self.assertEqual(self.client.post(self.url, data).status_code, 200)
        self.assertFalse(ScreenshotExclusion.objects.exists())

    def test_nothing_from_another_organization_can_be_excluded_or_removed(self):
        team_b = Team.objects.create(organization=self.org_b, name="Other")
        self.assertEqual(self.client.post(self.url, {"team": team_b.pk}).status_code, 200)
        self.assertEqual(self.client.post(self.url, {"employee": self.e_b.pk}).status_code, 200)
        other = services.add_exclusion(self.admin_b, self.org_b, user=self.e_b)
        self.assertEqual(self.client.post(reverse("screenshot_exclusion_remove", args=[other.pk])).status_code, 404)
        self.assertTrue(ScreenshotExclusion.objects.filter(pk=other.pk).exists())
        self.assertNotContains(self.client.get(self.url), "Bea Secret")

    def test_the_same_target_cannot_be_excluded_twice(self):
        self.client.post(self.url, {"team": self.team1.pk})
        self.assertEqual(self.client.post(self.url, {"team": self.team1.pk}).status_code, 200)
        self.assertEqual(ScreenshotExclusion.objects.count(), 1)

    def test_only_admins_manage_exclusions(self):
        exclusion = services.add_exclusion(self.admin_a, self.org_a, user=self.e3)
        for user in (self.manager1, self.e1, self.super_admin):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertEqual(self.client.post(reverse("screenshot_exclusion_remove", args=[exclusion.pk])).status_code, 403)
        self.assertTrue(ScreenshotExclusion.objects.filter(pk=exclusion.pk).exists())
