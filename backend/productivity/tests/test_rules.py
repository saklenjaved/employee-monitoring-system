from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import make_org, make_super_admin, make_user
from audit.models import AuditLog
from productivity.models import UNCLASSIFIED, ClassificationRule
from productivity.services import Classifier, change_rule_category, create_rule, delete_rule
from workforce.models import Team


class ClassifierTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.team1 = Team.objects.create(organization=cls.org_a, name="Support")
        cls.team2 = Team.objects.create(organization=cls.org_a, name="Sales")
        cls.team_b = Team.objects.create(organization=cls.org_b, name="Other")

    def rule(self, item_type, pattern, category, team=None, org=None):
        return create_rule(self.admin_a, org or self.org_a, item_type, pattern, category, team)

    def category(self, item_type, name, teams=()):
        return Classifier(self.org_a).category_for(item_type, name, [t.id for t in teams])

    def test_an_application_matches_by_exact_name_ignoring_case(self):
        self.rule("app", "chrome.exe", "productive")
        self.assertEqual(self.category("app", "Chrome.EXE"), "productive")
        self.assertEqual(self.category("app", "chrome2.exe"), UNCLASSIFIED)
        self.assertEqual(self.category("app", "hrome.exe"), UNCLASSIFIED)

    def test_a_website_rule_covers_subdomains_but_not_lookalikes(self):
        self.rule("website", "example.com", "unproductive")
        self.assertEqual(self.category("website", "example.com"), "unproductive")
        self.assertEqual(self.category("website", "mail.example.com"), "unproductive")
        self.assertEqual(self.category("website", "notexample.com"), UNCLASSIFIED)
        self.assertEqual(self.category("website", "example.com.evil.test"), UNCLASSIFIED)

    def test_applications_and_websites_do_not_mix(self):
        self.rule("app", "example.com", "productive")
        self.assertEqual(self.category("website", "example.com"), UNCLASSIFIED)

    def test_the_most_specific_website_rule_wins(self):
        self.rule("website", "example.com", "productive")
        self.rule("website", "social.example.com", "unproductive")
        self.assertEqual(self.category("website", "social.example.com"), "unproductive")
        self.assertEqual(self.category("website", "mail.example.com"), "productive")

    def test_a_team_rule_wins_over_an_organization_rule_for_that_team_only(self):
        self.rule("app", "slack.exe", "neutral")
        self.rule("app", "slack.exe", "productive", team=self.team1)
        self.assertEqual(self.category("app", "slack.exe", [self.team1]), "productive")
        self.assertEqual(self.category("app", "slack.exe", [self.team2]), "neutral")
        self.assertEqual(self.category("app", "slack.exe"), "neutral")

    def test_a_team_rule_wins_even_if_the_organization_pattern_is_more_specific(self):
        self.rule("website", "mail.example.com", "productive")
        self.rule("website", "example.com", "unproductive", team=self.team1)
        self.assertEqual(self.category("website", "mail.example.com", [self.team1]), "unproductive")

    def test_with_several_teams_the_oldest_rule_wins_a_tie(self):
        first = self.rule("app", "game.exe", "unproductive", team=self.team1)
        self.rule("app", "game.exe", "productive", team=self.team2)
        self.assertEqual(self.category("app", "game.exe", [self.team2, self.team1]), first.category)

    def test_rules_of_another_organization_never_apply(self):
        self.rule("app", "chrome.exe", "productive", org=self.org_b)
        self.assertEqual(self.category("app", "chrome.exe"), UNCLASSIFIED)

    def test_a_team_rule_does_not_apply_to_people_outside_the_team(self):
        self.rule("app", "tool.exe", "neutral", team=self.team1)
        self.assertEqual(self.category("app", "tool.exe", [self.team1]), "neutral")
        self.assertEqual(self.category("app", "tool.exe", [self.team2]), UNCLASSIFIED)
        self.assertEqual(self.category("app", "tool.exe"), UNCLASSIFIED)

    def test_deep_subdomains_match_the_nearest_parent_rule(self):
        self.rule("website", "example.com", "productive")
        self.rule("website", "b.example.com", "unproductive")
        self.assertEqual(self.category("website", "a.b.example.com"), "unproductive")
        self.assertEqual(self.category("website", "a.c.example.com"), "productive")
        self.assertEqual(self.category("website", "com"), UNCLASSIFIED)


class RuleServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.team_b = Team.objects.create(organization=cls.org_b, name="Other")

    def test_changes_are_audited(self):
        rule = create_rule(self.admin_a, self.org_a, "app", "chrome.exe", "productive")
        change_rule_category(self.admin_a, rule, "unproductive")
        delete_rule(self.admin_a, rule)
        actions = list(AuditLog.objects.order_by("id").values_list("action", flat=True))
        self.assertEqual(
            actions, ["classification_rule.created", "classification_rule.changed", "classification_rule.deleted"]
        )
        changed = AuditLog.objects.get(action="classification_rule.changed")
        self.assertEqual(changed.detail, "app chrome.exe: productive -> unproductive")
        self.assertFalse(ClassificationRule.objects.exists())

    def test_a_team_of_another_organization_is_refused(self):
        with self.assertRaises(ValueError):
            create_rule(self.admin_a, self.org_a, "app", "chrome.exe", "productive", self.team_b)
        self.assertFalse(ClassificationRule.objects.exists())

    def test_the_database_refuses_a_second_rule_in_the_same_scope(self):
        from django.db import IntegrityError, transaction

        create_rule(self.admin_a, self.org_a, "app", "chrome.exe", "productive")
        with self.assertRaises(IntegrityError), transaction.atomic():
            create_rule(self.admin_a, self.org_a, "app", "chrome.exe", "neutral")


class RulePagesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.admin_b = make_user("admin@b.test", Role.ADMIN, cls.org_b)
        cls.manager_a = make_user("manager@a.test", Role.MANAGER, cls.org_a)
        cls.employee_a = make_user("e@a.test", Role.EMPLOYEE, cls.org_a)
        cls.team_a = Team.objects.create(organization=cls.org_a, name="Support")
        cls.team_b = Team.objects.create(organization=cls.org_b, name="Other")
        cls.url = reverse("rule_list")

    def setUp(self):
        self.client.force_login(self.admin_a)

    def post_rule(self, **overrides):
        data = {"item_type": "app", "pattern": "Chrome.exe", "category": "productive"}
        data.update(overrides)
        return self.client.post(self.url, data)

    def test_create_a_rule_normalizes_the_name_and_audits(self):
        self.assertRedirects(self.post_rule(), self.url)
        rule = ClassificationRule.objects.get()
        self.assertEqual((rule.organization, rule.pattern, rule.team), (self.org_a, "chrome.exe", None))
        self.assertTrue(AuditLog.objects.filter(action="classification_rule.created", organization=self.org_a).exists())

    def test_website_names_are_cleaned_up(self):
        self.post_rule(item_type="website", pattern="  WWW.Example.COM ")
        self.assertEqual(ClassificationRule.objects.get().pattern, "example.com")

    def test_bad_names_are_refused(self):
        for item_type, pattern in (
            ("website", "http://example.com/path"),
            ("website", "exa mple.com"),
            ("website", ""),
            ("app", "C:\\Windows\\notepad.exe"),
            ("app", "dir/tool.exe"),
            ("app", ""),
        ):
            with self.subTest(pattern=pattern):
                self.assertEqual(self.post_rule(item_type=item_type, pattern=pattern).status_code, 200)
        self.assertFalse(ClassificationRule.objects.exists())

    def test_unknown_category_is_refused(self):
        self.assertEqual(self.post_rule(category="awesome").status_code, 200)
        self.assertFalse(ClassificationRule.objects.exists())

    def test_duplicate_rule_in_the_same_scope_is_refused_but_a_team_rule_is_allowed(self):
        self.post_rule()
        self.assertEqual(self.post_rule(category="neutral").status_code, 200)
        self.post_rule(team=self.team_a.pk)
        self.assertEqual(ClassificationRule.objects.count(), 2)

    def test_a_team_of_another_organization_is_refused(self):
        self.assertEqual(self.post_rule(team=self.team_b.pk).status_code, 200)
        self.assertFalse(ClassificationRule.objects.exists())

    def test_change_and_delete(self):
        self.post_rule()
        rule = ClassificationRule.objects.get()
        self.client.post(reverse("rule_change", args=[rule.pk]), {"category": "unproductive"})
        rule.refresh_from_db()
        self.assertEqual(rule.category, "unproductive")
        self.client.post(reverse("rule_change", args=[rule.pk]), {"category": "bogus"})
        rule.refresh_from_db()
        self.assertEqual(rule.category, "unproductive")
        self.client.post(reverse("rule_delete", args=[rule.pk]))
        self.assertFalse(ClassificationRule.objects.exists())

    def test_another_organizations_rule_cannot_be_changed_or_deleted(self):
        rule = create_rule(self.admin_b, self.org_b, "app", "chrome.exe", "productive")
        self.assertEqual(self.client.post(reverse("rule_change", args=[rule.pk]), {"category": "neutral"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("rule_delete", args=[rule.pk])).status_code, 404)
        rule.refresh_from_db()
        self.assertEqual(rule.category, "productive")

    def test_the_list_shows_only_own_rules(self):
        create_rule(self.admin_b, self.org_b, "app", "secret-b.exe", "productive")
        create_rule(self.admin_a, self.org_a, "app", "mine.exe", "productive")
        page = self.client.get(self.url)
        self.assertContains(page, "mine.exe")
        self.assertNotContains(page, "secret-b.exe")

    def test_other_roles_and_anonymous_users_are_kept_out(self):
        rule = create_rule(self.admin_a, self.org_a, "app", "chrome.exe", "productive")
        for user in (self.manager_a, self.employee_a, make_super_admin()):
            self.client.force_login(user)
            for method, url in (
                ("get", self.url),
                ("post", self.url),
                ("post", reverse("rule_change", args=[rule.pk])),
                ("post", reverse("rule_delete", args=[rule.pk])),
            ):
                with self.subTest(role=user.role, url=url):
                    self.assertEqual(getattr(self.client, method)(url).status_code, 403)
        self.assertTrue(ClassificationRule.objects.filter(pk=rule.pk).exists())
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_get_is_not_allowed_on_change_and_delete(self):
        rule = create_rule(self.admin_a, self.org_a, "app", "chrome.exe", "productive")
        self.assertEqual(self.client.get(reverse("rule_delete", args=[rule.pk])).status_code, 405)
        self.assertEqual(self.client.get(reverse("rule_change", args=[rule.pk])).status_code, 405)
