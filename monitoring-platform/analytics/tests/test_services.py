from datetime import date, datetime, timezone
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import make_org, make_super_admin, make_user
from analytics.models import ItemUsageDaily
from analytics.services import unclassified_items, usage_for
from analytics.summaries import rebuild_day
from analytics.tests.helpers import activity, app, at, make_device, site
from audit.models import AuditLog
from monitoring.services import update_policy
from productivity.models import ClassificationRule
from productivity.services import change_rule_category, create_rule
from workforce.models import Team, UserTeam

DAY = date(2026, 11, 2)


class UsageReadTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.manager_a = make_user("manager@a.test", Role.MANAGER, cls.org_a)
        cls.e1 = make_user("e1@a.test", Role.EMPLOYEE, cls.org_a)
        cls.e2 = make_user("e2@a.test", Role.EMPLOYEE, cls.org_a)
        cls.admin_b = make_user("admin@b.test", Role.ADMIN, cls.org_b)
        cls.e_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b)
        cls.team = Team.objects.create(organization=cls.org_a, name="Support")
        for user in (cls.manager_a, cls.e1):
            UserTeam.objects.create(organization=cls.org_a, team=cls.team, user=user)
        cls.super_admin = make_super_admin()

        pc = make_device(cls.e1)
        app(pc, at(10, 2, 0), "chrome.exe", seconds=120)
        app(pc, at(10, 3, 0), "game.exe", seconds=60)
        app(pc, at(10, 3, 30), "mystery.exe", seconds=30)
        site(pc, at(10, 2, 0), "example.com", seconds=100)
        for minute in (1, 2, 3):
            activity(pc, at(10, minute, 0), "active", seconds=60)
        activity(pc, at(10, 4, 0), "idle", seconds=60)
        rebuild_day(cls.e1, DAY)
        app(make_device(cls.e2), at(10, 1, 0), "chrome.exe", seconds=60)
        rebuild_day(cls.e2, DAY)
        app(make_device(cls.e_b), at(10, 1, 0), "chrome.exe", seconds=60)
        rebuild_day(cls.e_b, DAY)

        create_rule(cls.admin_a, cls.org_a, "app", "chrome.exe", "productive")
        create_rule(cls.admin_a, cls.org_a, "app", "game.exe", "unproductive")

    def test_totals_categories_and_activity(self):
        data = usage_for(self.admin_a, self.e1, DAY)
        self.assertEqual([(i["name"], i["category"], i["seconds"]) for i in data["apps"]], [
            ("chrome.exe", "productive", 120),
            ("game.exe", "unproductive", 60),
            ("mystery.exe", "unclassified", 30),
        ])
        self.assertEqual([(i["name"], i["seconds"]) for i in data["websites"]], [("example.com", 100)])
        self.assertEqual(
            data["category_seconds"],
            {"productive": 120, "unproductive": 60, "neutral": 0, "unclassified": 30},
        )
        self.assertEqual((data["active_seconds"], data["idle_seconds"]), (180, 60))
        self.assertEqual(data["activity_percent"], 75.0)

    def test_the_category_split_never_adds_more_than_application_time(self):
        data = usage_for(self.admin_a, self.e1, DAY)
        self.assertEqual(sum(data["category_seconds"].values()), sum(i["seconds"] for i in data["apps"]))

    def test_a_changed_rule_applies_at_once_without_rebuilding(self):
        rule = ClassificationRule.objects.get(pattern="game.exe")
        change_rule_category(self.admin_a, rule, "neutral")
        data = usage_for(self.admin_a, self.e1, DAY)
        self.assertEqual(data["category_seconds"]["neutral"], 60)
        self.assertEqual(data["category_seconds"]["unproductive"], 0)

    def test_a_team_rule_changes_only_that_teams_view(self):
        create_rule(self.admin_a, self.org_a, "app", "chrome.exe", "unproductive", team=self.team)
        self.assertEqual(usage_for(self.admin_a, self.e1, DAY)["category_seconds"]["unproductive"], 180)
        # e2 is in no team, so the organization-wide rule still applies.
        self.assertEqual(usage_for(self.admin_a, self.e2, DAY)["category_seconds"]["productive"], 60)

    def test_a_date_range_adds_the_days(self):
        app(make_device(self.e2, "second"), at(10, 1, 0, day=3), "chrome.exe", seconds=60)
        rebuild_day(self.e2, date(2026, 11, 3))
        data = usage_for(self.admin_a, self.e2, DAY, date(2026, 11, 3))
        self.assertEqual(data["apps"][0]["seconds"], 120)
        self.assertEqual(usage_for(self.admin_a, self.e2, date(2026, 11, 3))["apps"][0]["seconds"], 60)

    def test_an_employee_with_no_data_gets_empty_totals(self):
        data = usage_for(self.admin_a, self.e2, date(2026, 1, 1))
        self.assertEqual(data["apps"], [])
        self.assertIsNone(data["activity_percent"])

    def test_who_may_see_whom(self):
        self.assertIsNotNone(usage_for(self.admin_a, self.e1, DAY))
        self.assertIsNotNone(usage_for(self.admin_a, self.e2, DAY))
        # A Manager sees the employees of managed teams only.
        self.assertIsNotNone(usage_for(self.manager_a, self.e1, DAY))
        self.assertIsNone(usage_for(self.manager_a, self.e2, DAY))
        # An Employee sees only themselves.
        self.assertIsNotNone(usage_for(self.e1, self.e1, DAY))
        self.assertIsNone(usage_for(self.e1, self.e2, DAY))
        # Nobody sees another organization, and the Super Admin sees no employee data.
        self.assertIsNone(usage_for(self.admin_a, self.e_b, DAY))
        self.assertIsNone(usage_for(self.admin_b, self.e1, DAY))
        self.assertIsNone(usage_for(self.super_admin, self.e1, DAY))

    def test_unclassified_items_are_listed_and_exclude_anything_with_a_rule(self):
        names = {(i["item_type"], i["name"]) for i in unclassified_items(self.org_a, days=3650)}
        self.assertEqual(names, {("app", "mystery.exe"), ("website", "example.com")})

    def test_a_team_only_rule_still_counts_as_classified(self):
        create_rule(self.admin_a, self.org_a, "app", "mystery.exe", "neutral", team=self.team)
        names = {i["name"] for i in unclassified_items(self.org_a, days=3650)}
        self.assertNotIn("mystery.exe", names)

    def test_unclassified_items_show_time_and_employee_count_and_stay_inside_the_organization(self):
        app(make_device(self.e2, "p2"), at(11, 0, 30), "mystery.exe", seconds=30)
        rebuild_day(self.e2, DAY)
        item = next(i for i in unclassified_items(self.org_a, days=3650) if i["name"] == "mystery.exe")
        self.assertEqual((item["seconds"], item["employees"]), (60, 2))
        # Organization B has chrome.exe but no rule, and it must not appear for A.
        self.assertIn("chrome.exe", {i["name"] for i in unclassified_items(self.org_b, days=3650)})
        self.assertNotIn("chrome.exe", {i["name"] for i in unclassified_items(self.org_a, days=3650)})


class UnclassifiedRulesTests(TestCase):
    """An item is unclassified if at least one employee who uses it has no rule that applies to them."""

    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org")
        cls.admin = make_user("admin@o.test", Role.ADMIN, cls.org)
        cls.in_team = make_user("in@o.test", Role.EMPLOYEE, cls.org)
        cls.outside = make_user("out@o.test", Role.EMPLOYEE, cls.org)
        cls.team = Team.objects.create(organization=cls.org, name="Support")
        UserTeam.objects.create(organization=cls.org, team=cls.team, user=cls.in_team)
        for user in (cls.in_team, cls.outside):
            ItemUsageDaily.objects.create(
                organization=cls.org, user=user, date=date(2026, 11, 2), item_type="app", name="tool.exe", seconds=60
            )

    def listed(self, **kwargs):
        with mock.patch("analytics.services.timezone.now", return_value=datetime(2026, 11, 2, 12, tzinfo=timezone.utc)):
            return unclassified_items(self.org, **kwargs)

    def test_a_team_only_rule_leaves_the_item_listed_for_the_employees_it_does_not_cover(self):
        create_rule(self.admin, self.org, "app", "tool.exe", "neutral", team=self.team)
        items = self.listed()
        self.assertEqual([(i["name"], i["employees"], i["seconds"]) for i in items], [("tool.exe", 1, 60)])

    def test_an_organization_rule_clears_the_item(self):
        create_rule(self.admin, self.org, "app", "tool.exe", "neutral")
        self.assertEqual(self.listed(), [])

    def test_the_list_is_cut_to_the_limit_busiest_first(self):
        ItemUsageDaily.objects.create(
            organization=self.org, user=self.in_team, date=date(2026, 11, 2), item_type="app", name="busy.exe", seconds=500
        )
        self.assertEqual([i["name"] for i in self.listed(limit=1)], ["busy.exe"])

    def test_the_30_days_follow_indian_standard_time(self):
        ItemUsageDaily.objects.create(
            organization=self.org, user=self.in_team, date=date(2026, 11, 1), item_type="app", name="old.exe", seconds=10
        )
        ItemUsageDaily.objects.create(
            organization=self.org, user=self.in_team, date=date(2026, 11, 2), item_type="app", name="inside.exe", seconds=10
        )
        late_utc = datetime(2026, 11, 30, 23, 30, tzinfo=timezone.utc)  # already 1 Dec in India
        with mock.patch("analytics.services.timezone.now", return_value=late_utc):
            names = {i["name"] for i in unclassified_items(self.org, days=30)}
        self.assertIn("inside.exe", names)  # 2 Nov to 1 Dec is the last 30 days
        self.assertNotIn("old.exe", names)


class UnclassifiedPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.manager_a = make_user("manager@a.test", Role.MANAGER, cls.org_a)
        cls.e_a = make_user("e@a.test", Role.EMPLOYEE, cls.org_a)
        cls.e_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b)
        today = date.today()
        for user, name in ((cls.e_a, "mystery.exe"), (cls.e_b, "secret-b.exe")):
            ItemUsageDaily.objects.create(
                organization=user.organization, user=user, date=today, item_type="app", name=name, seconds=90
            )
        cls.url = reverse("unclassified_list")

    def setUp(self):
        self.client.force_login(self.admin_a)

    def test_page_lists_own_unclassified_items_only(self):
        page = self.client.get(self.url)
        self.assertContains(page, "mystery.exe")
        self.assertNotContains(page, "secret-b.exe")

    def test_classifying_creates_an_organization_rule_and_the_item_disappears(self):
        response = self.client.post(
            reverse("unclassified_classify"), {"item_type": "app", "name": "mystery.exe", "category": "neutral"}
        )
        self.assertRedirects(response, self.url)
        rule = ClassificationRule.objects.get()
        self.assertEqual((rule.organization, rule.pattern, rule.category, rule.team), (self.org_a, "mystery.exe", "neutral", None))
        self.assertTrue(AuditLog.objects.filter(action="classification_rule.created").exists())
        self.assertNotContains(self.client.get(self.url), "mystery.exe")

    def test_bad_input_creates_nothing(self):
        for data in (
            {"item_type": "app", "name": "mystery.exe", "category": "bogus"},
            {"item_type": "gadget", "name": "mystery.exe", "category": "neutral"},
            {"item_type": "app", "name": "dir/evil.exe", "category": "neutral"},
        ):
            self.client.post(reverse("unclassified_classify"), data)
        self.assertFalse(ClassificationRule.objects.exists())

    def test_only_admins_may_use_the_page(self):
        for user in (self.manager_a, self.e_a, make_super_admin()):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertEqual(self.client.post(reverse("unclassified_classify"), {}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
