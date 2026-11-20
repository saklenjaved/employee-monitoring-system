from datetime import date, datetime, timedelta, timezone
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone as dj_timezone

from accounts.models import Role
from accounts.tests.factories import make_org, make_super_admin, make_user
from analytics import services
from analytics.models import ActivityDaily, ItemUsageDaily
from analytics.templatetags.analytics_extras import category_label, duration, percent, share
from analytics.tests.helpers import make_device
from audit.models import AuditLog
from audit.services import record_event
from monitoring.models import MonitoringPolicy
from monitoring.services import update_policy
from productivity.services import create_rule
from workforce.models import Department, EmployeeProfile, Team, UserTeam

DAY = date(2026, 11, 2)
NOON = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
RANGE = {"start": "2026-11-02", "end": "2026-11-02"}


def usage(user, name, seconds, item_type="app", day=DAY):
    return ItemUsageDaily.objects.create(
        organization=user.organization, user=user, date=day, item_type=item_type, name=name, seconds=seconds
    )


def activity(user, active, idle, day=DAY):
    return ActivityDaily.objects.create(
        organization=user.organization, user=user, date=day, active_seconds=active, idle_seconds=idle
    )


class DashboardTestCase(TestCase):
    """Organization A has two teams. Organization B must never show up in A's pages."""

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
        cls.e4 = make_user("e4@a.test", Role.EMPLOYEE, cls.org_a, "Ed Four")
        cls.e_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b, "Bea Secret")
        cls.super_admin = make_super_admin()

        cls.team1 = Team.objects.create(organization=cls.org_a, name="Support")
        cls.team2 = Team.objects.create(organization=cls.org_a, name="Sales")
        cls.team_b = Team.objects.create(organization=cls.org_b, name="Other")
        for team, users in ((cls.team1, (cls.manager1, cls.e1, cls.e2)), (cls.team2, (cls.manager2, cls.e3))):
            for user in users:
                UserTeam.objects.create(organization=cls.org_a, team=team, user=user)
        UserTeam.objects.create(organization=cls.org_b, team=cls.team_b, user=cls.e_b)

        cls.dept = Department.objects.create(organization=cls.org_a, name="Customer Care")
        for user in (cls.e1, cls.e2):
            EmployeeProfile.objects.create(user=user, organization=cls.org_a, department=cls.dept)

        create_rule(cls.admin_a, cls.org_a, "app", "chrome.exe", "productive")
        create_rule(cls.admin_a, cls.org_a, "app", "game.exe", "neutral")
        create_rule(cls.admin_a, cls.org_a, "app", "game.exe", "unproductive", team=cls.team1)

        usage(cls.e1, "chrome.exe", 60)
        usage(cls.e1, "game.exe", 60)
        usage(cls.e1, "example.com", 100, "website")
        activity(cls.e1, 90, 30)
        usage(cls.e2, "chrome.exe", 120)
        activity(cls.e2, 100, 20)
        usage(cls.e3, "game.exe", 60)
        usage(cls.e3, "mystery.exe", 40)
        activity(cls.e3, 50, 50)
        usage(cls.e_b, "secret-b.exe", 999)
        activity(cls.e_b, 999, 1)

    def get(self, user, name, args=(), **query):
        self.client.force_login(user)
        return self.client.get(reverse(name, args=args), {**RANGE, **query})


class ReportServiceTests(DashboardTestCase):
    def test_team_report_totals_and_members(self):
        report = services.team_report(self.admin_a, self.team1.pk, DAY)
        totals = report["totals"]
        self.assertEqual(totals["app_seconds"], 240)
        self.assertEqual(totals["category_seconds"]["productive"], 180)
        self.assertEqual(totals["category_seconds"]["unproductive"], 60)  # the team rule beats the neutral one
        self.assertEqual(totals["productive_percent"], 75.0)
        self.assertEqual((totals["active_seconds"], totals["idle_seconds"]), (190, 50))
        self.assertEqual(totals["activity_percent"], 79.2)
        self.assertEqual([row["user"] for row in report["employees"]], [self.e2, self.e1])  # busiest first
        self.assertEqual(report["apps"][0]["name"], "chrome.exe")
        self.assertEqual(report["apps"][0]["employees"], 2)

    def test_the_same_application_can_be_mixed_across_teams(self):
        report = services.organization_report(self.admin_a, DAY)
        game = next(item for item in report["apps"] if item["name"] == "game.exe")
        self.assertEqual((game["category"], game["employees"], game["seconds"]), ("mixed", 2, 120))

    def test_who_may_open_a_team_report(self):
        self.assertIsNotNone(services.team_report(self.manager1, self.team1.pk, DAY))
        self.assertIsNone(services.team_report(self.manager1, self.team2.pk, DAY))
        self.assertIsNone(services.team_report(self.e1, self.team1.pk, DAY))
        self.assertIsNone(services.team_report(self.super_admin, self.team1.pk, DAY))
        self.assertIsNone(services.team_report(self.admin_a, self.team_b.pk, DAY))
        self.assertIsNone(services.team_report(self.admin_b, self.team1.pk, DAY))

    def test_teams_overview(self):
        names = lambda viewer: [row["team"].name for row in services.teams_overview(viewer, DAY)]  # noqa: E731
        self.assertEqual(names(self.admin_a), ["Sales", "Support"])
        self.assertEqual(names(self.manager1), ["Support"])
        self.assertEqual(names(self.e1), [])
        self.assertEqual(names(self.super_admin), [])

    def test_an_employee_in_two_teams_counts_in_both(self):
        UserTeam.objects.create(organization=self.org_a, team=self.team2, user=self.e1)
        rows = {row["team"].name: row for row in services.teams_overview(self.admin_a, DAY)}
        self.assertEqual(rows["Support"]["employee_count"], 2)
        self.assertEqual(rows["Sales"]["employee_count"], 2)

    def test_organization_report(self):
        report = services.organization_report(self.admin_a, DAY)
        self.assertEqual(report["employee_count"], 4)
        self.assertEqual(report["totals"]["app_seconds"], 340)
        self.assertEqual({row["team"].name for row in report["teams"]}, {"Support", "Sales"})
        departments = {row["name"]: row["employee_count"] for row in report["departments"]}
        self.assertEqual(departments, {"Customer Care": 2, "No department": 2})
        self.assertNotIn("secret-b.exe", {item["name"] for item in report["apps"]})
        self.assertNotIn(self.e_b, [row["user"] for row in report["employees"]])
        # mystery.exe and example.com have no rule. The count looks at the last 30 days, so the
        # date is fixed here to keep this test from depending on the day it runs.
        with mock.patch("analytics.services.timezone.now", return_value=NOON):
            self.assertEqual(services.organization_report(self.admin_a, DAY)["unclassified_count"], 2)

    def test_only_an_admin_gets_the_organization_report(self):
        for user in (self.manager1, self.e1, self.super_admin):
            self.assertIsNone(services.organization_report(user, DAY))

    def test_agent_status_counts(self):
        online, offline, revoked = (make_device(self.e1, n) for n in ("online", "offline", "revoked"))
        online.last_seen_at = dj_timezone.now()
        offline.last_seen_at = dj_timezone.now() - timedelta(hours=2)
        revoked.is_active = False
        for device in (online, offline, revoked):
            device.save()
        make_device(self.e_b, "other-org")
        self.assertEqual(services.agent_status(self.org_a), {"total": 3, "online": 1, "offline": 1, "revoked": 1})

    def test_platform_report_holds_counts_only(self):
        online = make_device(self.e1)
        online.last_seen_at = dj_timezone.now()
        online.save()
        make_device(self.e_b)
        self.org_b.is_active = False
        self.org_b.save()
        record_event(self.admin_a, "department.created", organization=self.org_a)
        record_event(self.e1, "device.registered", organization=self.org_a)  # employee action: left out
        report = services.platform_report(self.super_admin)
        self.assertEqual(
            (report["organization_count"], report["active_organization_count"], report["inactive_organization_count"]),
            (2, 1, 1),
        )
        self.assertEqual((report["employee_count"], report["admin_count"]), (5, 2))
        self.assertEqual((report["device_count"], report["online_count"]), (2, 1))
        actions = [entry.action for entry in report["recent_activity"]]
        self.assertIn("department.created", actions)
        self.assertNotIn("device.registered", actions)
        self.assertFalse({"apps", "websites", "employees", "totals"} & set(report))

    def test_only_the_super_admin_gets_the_platform_report(self):
        for user in (self.admin_a, self.manager1, self.e1):
            self.assertIsNone(services.platform_report(user))


class AccessTests(DashboardTestCase):
    def test_access_matrix(self):
        urls = {
            "me": reverse("dashboard_me"),
            "employee": reverse("dashboard_employee", args=[self.e1.pk]),
            "teams": reverse("dashboard_teams"),
            "team": reverse("dashboard_team", args=[self.team1.pk]),
            "organization": reverse("dashboard_organization"),
            "platform": reverse("dashboard_platform"),
        }
        expected = {
            self.e1: {"me": 200, "employee": 403, "teams": 403, "team": 403, "organization": 403, "platform": 403},
            self.manager1: {"me": 403, "employee": 200, "teams": 200, "team": 200, "organization": 403, "platform": 403},
            self.admin_a: {"me": 403, "employee": 200, "teams": 403, "team": 200, "organization": 200, "platform": 403},
            self.super_admin: {"me": 403, "employee": 403, "teams": 403, "team": 403, "organization": 403, "platform": 200},
        }
        for user, statuses in expected.items():
            self.client.force_login(user)
            for name, status in statuses.items():
                with self.subTest(role=user.role, page=name):
                    self.assertEqual(self.client.get(urls[name], RANGE).status_code, status)

    def test_anonymous_users_are_sent_to_login(self):
        for name in ("dashboard", "dashboard_me", "dashboard_teams", "dashboard_organization", "dashboard_platform"):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.url.startswith(reverse("login")))

    def test_dashboard_sends_each_role_to_its_own_page(self):
        targets = {
            self.e1: "dashboard_me",
            self.manager1: "dashboard_teams",
            self.admin_a: "dashboard_organization",
            self.super_admin: "dashboard_platform",
        }
        for user, name in targets.items():
            self.client.force_login(user)
            self.assertRedirects(self.client.get(reverse("dashboard")), reverse(name))

    def test_a_manager_sees_only_assigned_teams_and_their_employees(self):
        self.client.force_login(self.manager1)
        self.assertEqual(self.client.get(reverse("dashboard_team", args=[self.team2.pk]), RANGE).status_code, 404)
        self.assertEqual(self.client.get(reverse("dashboard_employee", args=[self.e3.pk]), RANGE).status_code, 404)
        self.assertEqual(self.client.get(reverse("dashboard_employee", args=[self.e4.pk]), RANGE).status_code, 404)
        listing = self.client.get(reverse("dashboard_teams"), RANGE)
        self.assertContains(listing, "Support")
        self.assertNotContains(listing, "Sales")
        team_page = self.client.get(reverse("dashboard_team", args=[self.team1.pk]), RANGE)
        self.assertContains(team_page, "Erin One")
        self.assertNotContains(team_page, "Eve Three")

    def test_an_admin_cannot_open_pages_of_another_organization(self):
        self.client.force_login(self.admin_a)
        self.assertEqual(self.client.get(reverse("dashboard_team", args=[self.team_b.pk]), RANGE).status_code, 404)
        self.assertEqual(self.client.get(reverse("dashboard_employee", args=[self.e_b.pk]), RANGE).status_code, 404)

    def test_an_admin_cannot_open_a_manager_as_an_employee(self):
        self.client.force_login(self.admin_a)
        self.assertEqual(self.client.get(reverse("dashboard_employee", args=[self.manager1.pk]), RANGE).status_code, 404)


class PageContentTests(DashboardTestCase):
    def test_employee_sees_only_own_numbers(self):
        page = self.get(self.e1, "dashboard_me")
        self.assertContains(page, "chrome.exe")
        self.assertContains(page, "example.com")
        self.assertNotContains(page, "mystery.exe")
        self.assertNotContains(page, "secret-b.exe")
        self.assertEqual(page.context["report"]["app_seconds"], 120)

    def test_organization_page_shows_own_data_only(self):
        page = self.get(self.admin_a, "dashboard_organization")
        for text in ("Org A", "Support", "Sales", "Customer Care", "Erin One", "chrome.exe", "mystery.exe"):
            self.assertContains(page, text)
        for text in ("Org B", "Bea Secret", "secret-b.exe", "Other"):
            self.assertNotContains(page, text)

    def test_admin_times_use_the_organization_time_zone(self):
        MonitoringPolicy.objects.create(organization=self.org_a, timezone="Asia/Kolkata")
        page = self.get(self.admin_a, "dashboard_organization")
        self.assertEqual(page.context["zone_label"], "IST")
        self.assertContains(page, "Updated")
        self.assertContains(page, "IST")

    def test_platform_page_shows_counts_and_no_employee_data(self):
        page = self.get(self.super_admin, "dashboard_platform")
        self.assertContains(page, "Org A")
        self.assertContains(page, "Org B")
        for text in ("Erin One", "e1@a.test", "chrome.exe", "Bea Secret"):
            self.assertNotContains(page, text)

    def test_pages_say_so_when_there_is_no_data(self):
        page = self.get(self.e4, "dashboard_me")
        self.assertContains(page, "No application time recorded")
        self.assertContains(page, "No applications recorded")

    def test_page_text_is_escaped(self):
        usage(self.e4, "<script>alert(1)</script>.exe", 30)
        page = self.get(self.e4, "dashboard_me")
        self.assertNotContains(page, "<script>alert(1)")
        self.assertContains(page, "&lt;script&gt;")


class DateRangeTests(DashboardTestCase):
    def test_a_range_adds_the_days(self):
        usage(self.e1, "chrome.exe", 30, day=date(2026, 11, 3))
        page = self.get(self.e1, "dashboard_me", start="2026-11-02", end="2026-11-03")
        self.assertEqual(page.context["report"]["app_seconds"], 150)

    def test_one_date_alone_means_that_day(self):
        usage(self.e1, "chrome.exe", 30, day=date(2026, 11, 3))
        self.client.force_login(self.e1)
        page = self.client.get(reverse("dashboard_me"), {"start": "2026-11-03"})
        self.assertEqual(page.context["report"]["app_seconds"], 30)

    def test_bad_ranges_fall_back_to_today_and_show_the_problem(self):
        for query in (
            {"start": "2026-11-05", "end": "2026-11-02"},
            {"start": "2026-01-01", "end": "2026-12-31"},
            {"start": "not-a-date", "end": "2026-11-02"},
        ):
            with self.subTest(query=query):
                self.client.force_login(self.e1)
                # "Today" is fixed to a day with no data, so the test does not depend on the date it runs.
                with mock.patch("analytics.dashboards._today_for", return_value=date(2026, 12, 25)):
                    page = self.client.get(reverse("dashboard_me"), query)
                self.assertEqual(page.status_code, 200)
                self.assertEqual(page.context["start"], page.context["end"])
                self.assertTrue(page.context["form"].errors)
                self.assertEqual(page.context["report"]["app_seconds"], 0)

    def test_today_follows_the_organization_time_zone(self):
        from analytics.dashboards import _today_for

        late_evening_utc = datetime(2026, 11, 2, 20, 0, tzinfo=timezone.utc)  # already 3 Nov in India
        with mock.patch("analytics.dashboards.timezone.now", return_value=late_evening_utc):
            self.assertEqual(_today_for(self.org_a), date(2026, 11, 2))  # no policy yet: UTC
            update_policy(self.admin_a, self.org_a, {"timezone": "Asia/Kolkata"})
            self.assertEqual(_today_for(self.org_a), date(2026, 11, 3))

    def test_the_page_opens_on_today(self):
        usage(self.e1, "tz.exe", 45, day=date(2026, 11, 3))
        self.client.force_login(self.e1)
        with mock.patch("analytics.dashboards._today_for", return_value=date(2026, 11, 3)):
            page = self.client.get(reverse("dashboard_me"))
        self.assertEqual(page.context["start"], date(2026, 11, 3))
        self.assertEqual(page.context["report"]["app_seconds"], 45)


class ReadOnlyPageTests(DashboardTestCase):
    def test_opening_dashboards_does_not_create_policy_rows(self):
        from monitoring.models import MonitoringPolicy

        for user, name in ((self.e1, "dashboard_me"), (self.admin_a, "dashboard_organization")):
            self.get(user, name)
        self.assertFalse(MonitoringPolicy.objects.exists())


class HomeAndNavigationTests(DashboardTestCase):
    def test_home_links_to_the_dashboard(self):
        self.client.force_login(self.e1)
        self.assertContains(self.client.get(reverse("home")), reverse("dashboard"))

    def test_audit_log_is_not_written_for_viewing_dashboards(self):
        before = AuditLog.objects.count()
        self.get(self.admin_a, "dashboard_organization")
        self.assertEqual(AuditLog.objects.count(), before)


class TemplateFilterTests(SimpleTestCase):
    def test_duration(self):
        self.assertEqual(
            [duration(v) for v in (0, 45, 185, 3600, 7500, None, 44.6)],
            ["0s", "45s", "3m 05s", "1h 00m", "2h 05m", "-", "45s"],
        )

    def test_percent(self):
        self.assertEqual([percent(v) for v in (None, 62.5, 50.0, 0)], ["-", "62.5%", "50%", "0%"])

    def test_share(self):
        self.assertEqual([share(1, 4), share(0, 0), share(5, 5), share(1, 3)], [25, 0, 100, 33])

    def test_category_label(self):
        self.assertEqual([category_label(k) for k in ("productive", "mixed", "other")], ["Productive", "Mixed", "other"])


class MonitoringOverviewTests(DashboardTestCase):
    """Real agent state: a PC is Active only after the agent confirmed the start and reports it is collecting."""

    def setUp(self):
        from devices.models import MonitoringStart

        now = dj_timezone.now()

        def confirmed_device(user, name, state):
            device = make_device(user, name)
            device.last_seen_at = now
            device.monitoring_state = state
            device.save()
            MonitoringStart.objects.create(
                organization=user.organization, user=user, device=device, status="confirmed",
                expires_at=now + timedelta(hours=1), confirmed_at=now, authorized_until=now + timedelta(hours=1),
            )

        confirmed_device(self.e1, "pc-e1", "active")  # Active
        confirmed_device(self.e2, "pc-e2", "paused_hours")  # Paused
        offline = make_device(self.e4, "pc-e4")  # Inactive: the PC has not checked in lately
        offline.last_seen_at = now - timedelta(hours=3)
        offline.save()
        # e3 has no PC at all: Inactive.

    def test_statuses_and_counts_come_from_the_agent_state(self):
        overview = services.monitoring_overview(self.admin_a)
        groups = {row["name"]: row["group"] for row in overview["employees"]}
        self.assertEqual(groups["Erin One"], "active")
        self.assertEqual(groups["Eli Two"], "paused")
        self.assertEqual(groups["Eve Three"], "inactive")
        self.assertEqual(groups["Ed Four"], "inactive")
        self.assertEqual(overview["active_count"], 1)
        self.assertNotIn("Bea Secret", {row["name"] for row in overview["employees"]})

    def test_team_counts_and_zero_monitored_flag(self):
        teams = {row["name"]: row for row in services.monitoring_overview(self.admin_a)["teams"]}
        self.assertEqual(teams["Support"], {"name": "Support", "total": 2, "monitored": 1, "none_monitored": False})
        self.assertEqual(teams["Sales"], {"name": "Sales", "total": 1, "monitored": 0, "none_monitored": True})
        self.assertNotIn("Other", teams)

    def test_only_an_admin_gets_the_overview(self):
        self.assertIsNone(services.monitoring_overview(self.manager1))
        self.assertIsNone(services.monitoring_overview(self.e1))

    def test_page_shows_labels_and_flags_without_other_organizations(self):
        page = self.get(self.admin_a, "dashboard_organization")
        for text in ("Monitoring status", "Active", "Paused", "Inactive", "None monitored"):
            self.assertContains(page, text)
        self.assertNotContains(page, "Bea Secret")


class MonitoringGroupAndExportTests(TestCase):
    """The overview counts filter the employee table, and the CSV export is limited and audited."""

    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.emp_a = make_user("erin@a.test", Role.EMPLOYEE, cls.org_a)
        cls.emp_a.full_name = "Erin Filter"
        cls.emp_a.save()
        cls.emp_b = make_user("bea@b.test", Role.EMPLOYEE, cls.org_b)
        cls.emp_b.full_name = "Bea Secret"
        cls.emp_b.save()
        cls.employee = make_user("worker@a.test", Role.EMPLOYEE, cls.org_a)

    def setUp(self):
        self.client.force_login(self.admin_a)

    def test_the_inactive_group_shows_the_employee_and_active_hides_them(self):
        page = self.client.get(reverse("dashboard_organization"), {"group": "inactive"}, secure=True)
        names = [row["user"].full_name for row in page.context["monitoring_rows"]]
        self.assertIn("Erin Filter", names)
        self.assertNotIn("Bea Secret", names)
        self.assertContains(page, "Showing: <strong>Not monitored now</strong>")
        page = self.client.get(reverse("dashboard_organization"), {"group": "active"}, secure=True)
        self.assertNotIn("Erin Filter", [row["user"].full_name for row in page.context["monitoring_rows"]])

    def test_an_unknown_group_shows_everyone(self):
        page = self.client.get(reverse("dashboard_organization"), {"group": "bogus"}, secure=True)
        self.assertEqual(page.context["monitoring_group"], "")
        self.assertIn("Erin Filter", [row["user"].full_name for row in page.context["monitoring_rows"]])

    def test_the_export_has_names_and_status_but_no_emails(self):
        response = self.client.get(reverse("dashboard_organization_export"), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        body = response.content.decode()
        self.assertIn("Employee,Team,Monitoring,Agent status", body)
        self.assertIn("Erin Filter", body)
        self.assertNotIn("erin@a.test", body)
        self.assertNotIn("Bea Secret", body)

    def test_the_export_is_audited_with_a_count_and_no_names(self):
        self.client.get(reverse("dashboard_organization_export"), secure=True)
        entry = AuditLog.objects.get(action="monitoring_status.exported")
        self.assertEqual(entry.organization, self.org_a)
        self.assertIn("employees", entry.detail)
        self.assertNotIn("Erin", entry.detail)

    def test_employees_cannot_export(self):
        self.client.force_login(self.employee)
        response = self.client.get(reverse("dashboard_organization_export"), secure=True)
        self.assertNotEqual(response.status_code, 200)
        self.assertNotIn(b"Erin Filter", response.content)


class LiveActivityTests(TestCase):
    """The live activity list shows only the admin's own employees' reported app and website events."""

    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.emp_a = make_user("erin@a.test", Role.EMPLOYEE, cls.org_a)
        cls.emp_b = make_user("bea@b.test", Role.EMPLOYEE, cls.org_b)
        cls.device_a = make_device(cls.emp_a, "PC A")
        cls.device_b = make_device(cls.emp_b, "PC B")

    def test_the_admin_sees_own_app_and_website_events_only(self):
        from analytics.tests.helpers import app, site

        app(self.device_a, NOON, name="chrome.exe")
        site(self.device_a, NOON, domain="example.com")
        app(self.device_b, NOON, name="secret-b.exe")
        rows = services.live_activity(self.admin_a)
        names = {row["name"] for row in rows}
        self.assertIn("chrome.exe", names)
        self.assertIn("example.com", names)
        self.assertNotIn("secret-b.exe", names)
        self.assertEqual({row["kind"] for row in rows}, {"app", "website"})

    def test_other_roles_get_no_live_activity(self):
        employee = make_user("worker@a.test", Role.EMPLOYEE, self.org_a)
        self.assertEqual(services.live_activity(employee), [])

    def test_attention_count_adds_approvals_offline_and_not_signed_in(self):
        live = services.live_status(self.admin_a)
        expected = live["pending_approvals"] + live["not_signed_in_count"] + live["agents"]["offline"]
        self.assertEqual(live["attention_count"], expected)
