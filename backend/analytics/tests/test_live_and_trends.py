"""Chart data (trend.json), the live Admin panel, HTMX list attributes and the seed_demo command."""
from datetime import date, timedelta
from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role, User
from accounts.tests.factories import make_org, make_super_admin, make_user
from analytics import services
from analytics.models import ActivityDaily, ItemUsageDaily
from devices.models import Device
from organizations.models import Organization
from productivity.services import create_rule
from workforce.models import Team, UserTeam

DAY = date(2026, 11, 2)
RANGE = {"start": "2026-11-02", "end": "2026-11-02"}


def usage(user, name, seconds, day=DAY, item_type="app"):
    ItemUsageDaily.objects.create(
        organization=user.organization, user=user, date=day, item_type=item_type, name=name, seconds=seconds
    )


class TrendTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.admin_b = make_user("admin@b.test", Role.ADMIN, cls.org_b)
        cls.manager = make_user("m@a.test", Role.MANAGER, cls.org_a)
        cls.e1 = make_user("e1@a.test", Role.EMPLOYEE, cls.org_a)
        cls.e2 = make_user("e2@a.test", Role.EMPLOYEE, cls.org_a)  # not in the manager's team
        cls.e_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b)
        cls.super_admin = make_super_admin()
        cls.team = Team.objects.create(organization=cls.org_a, name="Support")
        cls.other_team = Team.objects.create(organization=cls.org_a, name="Sales")
        for user in (cls.manager, cls.e1):
            UserTeam.objects.create(organization=cls.org_a, team=cls.team, user=user)
        UserTeam.objects.create(organization=cls.org_a, team=cls.other_team, user=cls.e2)
        create_rule(cls.admin_a, cls.org_a, "app", "code.exe", "productive")
        create_rule(cls.admin_a, cls.org_a, "app", "steam.exe", "unproductive")
        usage(cls.e1, "code.exe", 2 * 3600)
        usage(cls.e1, "steam.exe", 1800)
        usage(cls.e1, "code.exe", 3600, day=DAY - timedelta(days=1))
        usage(cls.e1, "github.com", 999, item_type="website")  # website time is not application time
        usage(cls.e2, "unknown.exe", 3600)
        usage(cls.e_b, "code.exe", 9 * 3600)
        ActivityDaily.objects.create(organization=cls.org_a, user=cls.e1, date=DAY, active_seconds=3000, idle_seconds=1000)

    def get(self, user, name, *args):
        self.client.force_login(user)
        return self.client.get(reverse(name, args=args), RANGE)

    # --- Values ---------------------------------------------------------------------------

    def test_trend_covers_two_weeks_and_sums_hours_per_category(self):
        data = self.get(self.admin_a, "trend_organization").json()
        self.assertEqual(len(data["labels"]), services.TREND_MIN_DAYS)
        self.assertEqual(data["labels"][-1], "2026-11-02")
        self.assertEqual(data["series"]["productive"][-1], 2.0)
        self.assertEqual(data["series"]["productive"][-2], 1.0)
        self.assertEqual(data["series"]["unproductive"][-1], 0.5)
        self.assertEqual(data["series"]["unclassified"][-1], 1.0)
        self.assertEqual(data["activity_percent"][-1], 75.0)
        self.assertIsNone(data["activity_percent"][0])

    def test_a_longer_range_is_kept_as_chosen(self):
        self.client.force_login(self.admin_a)
        data = self.client.get(reverse("trend_organization"), {"start": "2026-10-01", "end": "2026-11-02"}).json()
        self.assertEqual(data["labels"][0], "2026-10-01")
        self.assertEqual(len(data["labels"]), 33)

    # --- Tenant isolation -----------------------------------------------------------------

    def test_another_organizations_data_never_appears_in_the_organization_trend(self):
        data = self.get(self.admin_b, "trend_organization").json()
        # Only Org B's own employee shows. Org A's rules do not apply in Org B, so it is unclassified there.
        self.assertEqual(data["series"]["unclassified"][-1], 9.0)
        self.assertEqual(data["series"]["productive"][-1], 0)
        self.assertEqual(data["series"]["unproductive"][-1], 0)

    def test_an_admin_cannot_open_another_organizations_employee_or_team_trend(self):
        self.assertEqual(self.get(self.admin_b, "trend_employee", self.e1.pk).status_code, 404)
        self.assertEqual(self.get(self.admin_b, "trend_team", self.team.pk).status_code, 404)

    # --- Roles ----------------------------------------------------------------------------

    def test_a_manager_sees_only_assigned_team_trends(self):
        self.assertEqual(self.get(self.manager, "trend_team", self.team.pk).status_code, 200)
        self.assertEqual(self.get(self.manager, "trend_team", self.other_team.pk).status_code, 404)
        self.assertEqual(self.get(self.manager, "trend_employee", self.e1.pk).status_code, 200)
        self.assertEqual(self.get(self.manager, "trend_employee", self.e2.pk).status_code, 404)
        self.assertEqual(self.get(self.manager, "trend_organization").status_code, 403)

    def test_an_employee_gets_only_their_own_trend(self):
        data = self.get(self.e1, "trend_me").json()
        self.assertEqual(data["series"]["productive"][-1], 2.0)
        self.assertEqual(self.get(self.e1, "trend_employee", self.e2.pk).status_code, 403)
        self.assertEqual(self.get(self.e1, "trend_organization").status_code, 403)
        self.assertEqual(self.get(self.e2, "trend_me").json()["series"]["productive"][-1], 0)

    def test_super_admin_gets_no_employee_level_trends(self):
        for name, args in (("trend_organization", ()), ("trend_employee", (self.e1.pk,)), ("trend_me", ())):
            with self.subTest(name=name):
                self.assertEqual(self.get(self.super_admin, name, *args).status_code, 403)

    def test_anonymous_users_are_sent_to_login(self):
        response = self.client.get(reverse("trend_organization"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    # --- Pages that hold the charts -------------------------------------------------------

    def test_dashboard_pages_point_their_chart_at_the_right_endpoint(self):
        cases = (
            (self.admin_a, "dashboard_organization", (), reverse("trend_organization")),
            (self.admin_a, "dashboard_team", (self.team.pk,), reverse("trend_team", args=[self.team.pk])),
            (self.admin_a, "dashboard_employee", (self.e1.pk,), reverse("trend_employee", args=[self.e1.pk])),
            (self.e1, "dashboard_me", (), reverse("trend_me")),
        )
        for user, page, args, url in cases:
            with self.subTest(page=page):
                self.assertContains(self.get(user, page, *args), f'data-trend-url="{url}?')


class LivePanelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.manager = make_user("m@a.test", Role.MANAGER, cls.org_a)
        cls.e1 = make_user("e1@a.test", Role.EMPLOYEE, cls.org_a)
        cls.e_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b)

    def make_device(self, user, online):
        from django.utils import timezone

        Device.objects.create(
            organization=user.organization, user=user, name=f"PC-{user.pk}-{online}", os_version="Windows 11",
            agent_version="0.1.0", credential_hash=f"{user.pk}-{online}".ljust(64, "x"),
            credential_expires_at=timezone.now() + timedelta(days=30),
            last_seen_at=timezone.now() - (timedelta(minutes=1) if online else timedelta(hours=3)),
        )

    def test_the_live_panel_counts_only_this_organizations_agents(self):
        self.make_device(self.e1, online=True)
        self.make_device(self.e1, online=False)
        self.make_device(self.e_b, online=True)
        self.client.force_login(self.admin_a)
        response = self.client.get(reverse("dashboard_live"), HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["live"]["agents"], {"total": 2, "online": 1, "offline": 1, "revoked": 0})
        self.assertNotContains(response, "<html")  # a partial, not a whole page

    def test_the_live_panel_shows_new_agent_data_on_the_next_poll(self):
        self.client.force_login(self.admin_a)
        before = self.client.get(reverse("dashboard_live")).context["live"]["agents"]["online"]
        self.make_device(self.e1, online=True)
        after = self.client.get(reverse("dashboard_live")).context["live"]["agents"]["online"]
        self.assertEqual((before, after), (0, 1))

    def test_only_admins_get_the_live_panel(self):
        for user in (self.manager, self.e1):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("dashboard_live")).status_code, 403)

    def test_the_admin_dashboard_polls_the_live_panel_every_10_seconds(self):
        self.client.force_login(self.admin_a)
        response = self.client.get(reverse("dashboard_organization"))
        self.assertContains(response, f'hx-get="{reverse("dashboard_live")}"')
        self.assertContains(response, 'hx-trigger="every 10s"')


class Monitoring360Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.e1 = make_user("e1@a.test", Role.EMPLOYEE, cls.org_a, "Ada Online")
        cls.e2 = make_user("e2@a.test", Role.EMPLOYEE, cls.org_a, "Bob Nothing")
        cls.e_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b, "Stranger Person")
        Device.objects.create(
            organization=cls.org_a, user=cls.e1, name="PC-1", os_version="Windows 11", agent_version="0.1.0",
            credential_hash="1".ljust(64, "x"), credential_expires_at=timezone.now() + timedelta(days=30),
            last_seen_at=timezone.now(), focus_app="code.exe", focus_title="main.py", queue_depth=3,
        )
        usage(cls.e1, "code.exe", 3600)

    def test_each_employee_gets_live_state_next_to_their_usage(self):
        self.client.force_login(self.admin_a)
        response = self.client.get(reverse("dashboard_organization"), RANGE)
        rows = {row["user"].pk: row for row in response.context["monitoring_rows"]}
        self.assertEqual(rows[self.e1.pk]["live"]["state"], "uploading")  # 3 queued items
        self.assertEqual(rows[self.e1.pk]["live"]["focus_app"], "code.exe")
        self.assertEqual(rows[self.e1.pk]["live"]["queue_depth"], 3)
        self.assertEqual(rows[self.e1.pk]["app_seconds"], 3600)
        self.assertEqual(rows[self.e2.pk]["live"]["state"], "not_connected")

    def test_the_live_table_shows_todays_app_time_and_activity(self):
        ItemUsageDaily.objects.create(
            organization=self.org_a, user=self.e1, date=timezone.now().date(), item_type="app", name="code.exe", seconds=1200
        )
        ActivityDaily.objects.create(
            organization=self.org_a, user=self.e1, date=timezone.now().date(), active_seconds=300, idle_seconds=100
        )
        self.client.force_login(self.admin_a)
        rows = {row["id"]: row for row in self.client.get(reverse("dashboard_live")).context["live"]["employees"]}
        self.assertEqual(rows[self.e1.pk]["app_seconds_today"], 1200)
        self.assertEqual(rows[self.e1.pk]["activity_percent_today"], 75.0)

    def test_the_360_table_lists_only_this_organizations_employees(self):
        self.client.force_login(self.admin_a)
        response = self.client.get(reverse("dashboard_organization"))
        self.assertContains(response, "Ada Online")
        self.assertContains(response, "Bob Nothing")
        self.assertNotContains(response, "Stranger Person")

    def test_the_admin_dashboard_shows_the_monitoring_intervals(self):
        from monitoring.models import MonitoringPolicy

        MonitoringPolicy.objects.create(
            organization=self.org_a, sample_interval_seconds=45, idle_threshold_seconds=600, sync_interval_seconds=120
        )
        self.client.force_login(self.admin_a)
        response = self.client.get(reverse("dashboard_organization"))
        self.assertContains(response, "45 s")
        self.assertContains(response, "600 s")
        self.assertContains(response, "120 s")

    def test_the_helper_matches_rows_by_employee_id(self):
        usage_row = {"user": self.e2, "app_seconds": 0}
        live_row = {"id": self.e2.pk, "state": "offline"}
        merged = services.monitoring_360_rows([usage_row], [live_row])
        self.assertEqual(merged[0]["live"], live_row)
        self.assertEqual(services.monitoring_360_rows([usage_row], [])[0]["live"]["state"], "not_connected")


class CommandCenterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.e1 = make_user("e1@a.test", Role.EMPLOYEE, cls.org_a)
        cls.e2 = make_user("e2@a.test", Role.EMPLOYEE, cls.org_a)
        cls.e2.is_active = False
        cls.e2.save()
        cls.e_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b)

    def make_device(self, user, online=True, **extra):
        from django.utils import timezone

        return Device.objects.create(
            organization=user.organization, user=user, name=f"PC-{user.pk}-{online}-{extra.get('last_error_code', '')}",
            os_version="Windows 11", agent_version="0.1.0", credential_hash=f"{user.pk}-{online}-{extra}".ljust(64, "y"),
            credential_expires_at=timezone.now() + timedelta(days=30),
            last_seen_at=timezone.now() - (timedelta(minutes=1) if online else timedelta(hours=3)), **extra,
        )

    def confirm(self, device, minutes=60):
        """A start the agent confirmed, still running."""
        from django.utils import timezone

        from devices.models import MonitoringStart

        MonitoringStart.objects.create(
            organization=device.organization, user=device.user, device=device, status=MonitoringStart.Status.CONFIRMED,
            expires_at=timezone.now(), confirmed_at=timezone.now(), authorized_until=timezone.now() + timedelta(minutes=minutes),
        )

    def test_counts_come_from_this_organizations_records(self):
        self.confirm(self.make_device(self.e1, online=True, monitoring_state="active"))
        self.make_device(self.e1, online=False)
        self.make_device(self.e_b, online=True)
        self.client.force_login(self.admin_a)
        center = self.client.get(reverse("dashboard_organization"), RANGE).context["command"]
        self.assertEqual(center["employees"], {"total": 2, "active": 1, "inactive": 1, "not_signed_in": 1})
        self.assertEqual(center["devices"]["registered"], 2)
        self.assertEqual(center["devices"]["online"], 1)
        self.assertEqual(center["devices"]["offline"], 1)
        self.assertEqual(center["monitoring"]["monitored_now"], 1)
        self.assertEqual(center["monitoring"]["not_monitored_now"], 0)

    def test_an_online_agent_without_a_confirmed_start_is_not_monitored(self):
        self.make_device(self.e1, online=True, monitoring_state="active")
        self.client.force_login(self.admin_a)
        center = self.client.get(reverse("dashboard_organization"), RANGE).context["command"]
        self.assertEqual(center["monitoring"]["monitored_now"], 0)

    def test_a_confirmed_start_outside_hours_counts_as_paused_not_monitored(self):
        self.confirm(self.make_device(self.e1, online=True, monitoring_state="paused_hours"))
        self.client.force_login(self.admin_a)
        center = self.client.get(reverse("dashboard_organization"), RANGE).context["command"]
        self.assertEqual((center["monitoring"]["monitored_now"], center["monitoring"]["paused_now"]), (0, 1))

    def test_inactive_employees_are_not_counted_as_monitored(self):
        self.confirm(self.make_device(self.e2, online=True, monitoring_state="active"))
        self.client.force_login(self.admin_a)
        center = self.client.get(reverse("dashboard_organization"), RANGE).context["command"]
        self.assertEqual(center["monitoring"]["monitored_now"], 0)

    def test_the_error_count_and_activity_feed_are_limited_to_this_organization(self):
        from audit.services import record_event

        self.make_device(self.e1, online=True, last_error_code="AGENT_FAILED")
        self.make_device(self.e_b, online=True, last_error_code="AGENT_FAILED")
        record_event(actor=self.admin_a, action="device.registered", organization=self.org_a, detail="PC-A")
        record_event(actor=self.admin_a, action="device.registered", organization=self.org_b, detail="PC-B")
        self.client.force_login(self.admin_a)
        center = self.client.get(reverse("dashboard_organization"), RANGE).context["command"]
        self.assertEqual(center["devices"]["with_errors"], 1)
        self.assertEqual([row["detail"] for row in center["activity"]], ["PC-A"])
        self.assertIn("successfully registered WinAgent", center["activity"][0]["text"])

    def test_only_admins_get_the_command_center_figures(self):
        from analytics.services import command_center

        self.assertIsNone(command_center(self.e1, date(2026, 11, 2)))

    def test_the_page_shows_the_command_center_sections(self):
        self.client.force_login(self.admin_a)
        response = self.client.get(reverse("dashboard_organization"), RANGE)
        self.assertContains(response, "Attention required")
        self.assertContains(response, "Device connections")
        self.assertContains(response, "Screenshot activity")
        self.assertContains(response, f'href="{reverse("screenshot_list")}"')


class HtmxListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        make_user("findme@a.test", Role.EMPLOYEE, cls.org_a, "Findable Person")
        make_user("hidden@b.test", Role.EMPLOYEE, cls.org_b, "Findable Stranger")

    def test_the_list_bar_searches_as_you_type_and_swaps_only_the_page_body(self):
        self.client.force_login(self.admin_a)
        response = self.client.get(reverse("person_list"))
        self.assertContains(response, 'hx-trigger="input delay:300ms, submit"')
        self.assertContains(response, 'hx-select="#main"')
        self.assertContains(response, 'id="list-search"')

    def test_an_htmx_search_returns_matches_from_this_organization_only(self):
        self.client.force_login(self.admin_a)
        response = self.client.get(reverse("person_list"), {"q": "Findable"}, HTTP_HX_REQUEST="true")
        self.assertContains(response, "Findable Person")
        self.assertNotContains(response, "Findable Stranger")


class SeedDemoTests(TestCase):
    @override_settings(DEBUG=True)
    @mock.patch("analytics.management.commands.seed_demo.DAYS", 3)  # keeps the test quick
    def test_seed_demo_creates_every_role_and_is_idempotent(self):
        call_command("seed_demo", stdout=StringIO())
        counts = (
            Organization.objects.count(),
            User.objects.count(),
            Device.objects.count(),
            ItemUsageDaily.objects.count(),
            ActivityDaily.objects.count(),
        )
        call_command("seed_demo", stdout=StringIO())
        self.assertEqual(
            counts,
            (
                Organization.objects.count(),
                User.objects.count(),
                Device.objects.count(),
                ItemUsageDaily.objects.count(),
                ActivityDaily.objects.count(),
            ),
        )
        self.assertEqual(
            set(User.objects.values_list("role", flat=True)),
            {Role.SUPER_ADMIN, Role.ADMIN, Role.MANAGER, Role.EMPLOYEE},
        )
        self.assertGreater(counts[3], 0)
        # Every seeded record stays inside its own organization.
        for device in Device.objects.select_related("user"):
            self.assertEqual(device.organization_id, device.user.organization_id)

    @override_settings(DEBUG=False)
    def test_seed_demo_refuses_to_run_outside_development(self):
        with mock.patch.dict("os.environ", {"DEMO_SEED_ALLOWED": ""}):
            with self.assertRaises(CommandError):
                call_command("seed_demo", stdout=StringIO())


class RecentActivityFeedTests(TestCase):
    """Recent activity is built from real audit records, in India time, newest first, for one organization."""

    @classmethod
    def setUpTestData(cls):
        from accounts.tests.factories import make_org, make_user

        cls.org = make_org("Feed Org")
        cls.admin = make_user("admin@feed.test", Role.ADMIN, cls.org, "Ada Admin")
        cls.emp = make_user("john@feed.test", Role.EMPLOYEE, cls.org, "John Employee")

    def test_the_real_monitoring_flow_is_described_newest_first(self):
        from devices.models import Device
        from devices.start import confirm_start, give_consent, request_approval
        from django.utils import timezone

        from analytics.services import recent_activity_feed
        from analytics.tests.helpers import make_device

        device = make_device(self.emp, "JOHN-PC")
        Device.objects.filter(pk=device.pk).update(last_seen_at=timezone.now())
        request_approval(self.emp, actor=self.admin)  # Admin approves monitoring
        start, _ = give_consent(self.emp)  # Employee approves monitoring
        confirm_start(device, str(start.id))  # the agent confirms it started
        texts = [row["text"] for row in recent_activity_feed(self.org)]
        self.assertEqual(
            texts[:3],
            [
                "Monitoring started successfully for John Employee",
                "John Employee approved monitoring",
                "Ada Admin approved monitoring for John Employee",
            ],
        )

    def test_device_registration_and_employee_activation_are_described(self):
        from audit.services import record_event
        from devices.models import Device

        from analytics.services import recent_activity_feed
        from analytics.tests.helpers import make_device

        device = make_device(self.emp, "JOHN-PC")
        record_event(self.admin, "user.created_in_admin", organization=self.org, target=self.emp)
        record_event(self.emp, "device.registered", organization=self.org, target=device)
        texts = [row["text"] for row in recent_activity_feed(self.org)]
        self.assertEqual(texts[:2], ["John Employee successfully registered WinAgent", "Ada Admin added employee John Employee"])
        self.assertIsNotNone(Device.objects.get(pk=device.pk))

    def test_times_are_shown_in_india_time_from_the_stored_utc_time(self):
        from zoneinfo import ZoneInfo

        from audit.services import record_event

        from analytics.services import recent_activity_feed

        record_event(self.admin, "organization.created", organization=self.org)
        row = recent_activity_feed(self.org)[0]
        local = row["at"].astimezone(ZoneInfo("Asia/Kolkata"))
        self.assertEqual(row["when_time"], local.strftime("%I:%M %p"))
        self.assertEqual(row["when_date"], f"{local.day} {local:%b %Y}")

    def test_the_feed_pages_five_activities_newest_first_with_no_duplicates(self):
        from audit.services import record_event

        from analytics.services import recent_activity_feed

        for index in range(7):
            record_event(self.admin, "organization.created", organization=self.org, detail=f"n{index}")
        first = recent_activity_feed(self.org, page=1)
        second = recent_activity_feed(self.org, page=2)
        self.assertEqual((len(first), len(second)), (5, 2))
        self.assertEqual(first.paginator.num_pages, 2)
        ids = [row["at"] for row in list(first) + list(second)]
        self.assertEqual(len(set(ids)), 7)
        self.assertGreater(first[0]["at"], second[0]["at"])

    def test_the_activity_pager_keeps_the_date_filter_and_the_empty_state_shows(self):
        from audit.services import record_event

        self.client.force_login(self.admin)
        empty = self.client.get(reverse("dashboard_organization"))
        self.assertContains(empty, "No account, device or monitoring activity yet.")
        for index in range(6):
            record_event(self.admin, "organization.created", organization=self.org, detail=f"n{index}")
        page = self.client.get(reverse("dashboard_organization"), {"start": "2026-11-01", "end": "2026-11-30"})
        self.assertContains(page, "page=2#recent-activity")
        self.assertContains(page, "start=2026-11-01")
        self.assertNotContains(page, "No account, device or monitoring activity yet.")

    def test_the_dashboard_refreshes_the_activity_card_every_15_seconds(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("dashboard_organization"))
        self.assertContains(page, 'id="recent-activity"')
        self.assertContains(page, 'hx-trigger="every 15s"')
        self.assertContains(page, 'hx-select="#recent-activity"')
