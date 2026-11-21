"""The app and website rules list: search, filters, sorting and empty states, limited to the Admin's organization."""
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from productivity.models import ClassificationRule
from productivity.services import create_rule
from workforce.models import Team


def patterns_on_page(response):
    return [rule.pattern for rule in response.context["listing"]["page"]]


class RuleListPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.admin_b = make_user("admin@b.test", Role.ADMIN, cls.org_b)
        cls.support = Team.objects.create(organization=cls.org_a, name="Support")
        create_rule(cls.admin_a, cls.org_a, "app", "chrome.exe", "productive")
        create_rule(cls.admin_a, cls.org_a, "website", "example.com", "unproductive")
        create_rule(cls.admin_a, cls.org_a, "website", "news.test", "neutral")
        create_rule(cls.admin_a, cls.org_a, "app", "tool.exe", "neutral", team=cls.support)
        create_rule(cls.admin_b, cls.org_b, "app", "rival.exe", "productive")
        cls.url = reverse("rule_list")

    def setUp(self):
        self.client.force_login(self.admin_a)

    def get(self, query_string=""):
        return self.client.get(f"{self.url}?{query_string}")

    def test_the_list_shows_only_this_organizations_rules_by_name(self):
        self.assertEqual(patterns_on_page(self.get()), ["chrome.exe", "example.com", "news.test", "tool.exe"])

    def test_search_matches_the_pattern_or_the_team_name(self):
        self.assertEqual(patterns_on_page(self.get("q=example")), ["example.com"])
        self.assertEqual(patterns_on_page(self.get("q=support")), ["tool.exe"])

    def test_another_organizations_rule_is_never_found(self):
        response = self.get("q=rival")
        self.assertNotContains(response, "rival.exe")
        self.assertContains(response, "No rules match your search or filters.")

    def test_type_and_category_filters(self):
        self.assertEqual(patterns_on_page(self.get("item_type=website")), ["example.com", "news.test"])
        self.assertEqual(patterns_on_page(self.get("category=unproductive")), ["example.com"])

    def test_applies_to_filter_separates_whole_organization_from_team_rules(self):
        self.assertEqual(patterns_on_page(self.get("applies_to=org")), ["chrome.exe", "example.com", "news.test"])
        self.assertEqual(patterns_on_page(self.get(f"applies_to={self.support.pk}")), ["tool.exe"])

    def test_sort_by_pattern_in_reverse(self):
        self.assertEqual(patterns_on_page(self.get("sort=name&dir=desc")), ["tool.exe", "news.test", "example.com", "chrome.exe"])

    def test_empty_organization_says_there_are_no_rules_yet(self):
        empty = make_org("Empty")
        self.client.force_login(make_user("admin@empty.test", Role.ADMIN, empty))
        self.assertContains(self.get(), "No rules yet.")

    def test_a_filter_with_no_match_offers_a_clear_link(self):
        response = self.get("category=productive&item_type=website")
        self.assertContains(response, "No rules match your search or filters.")
        self.assertContains(response, "Clear search and filters")

    def test_saving_a_rule_change_still_works_from_the_filtered_list(self):
        rule = ClassificationRule.objects.get(pattern="chrome.exe", organization=self.org_a)
        self.client.post(reverse("rule_change", args=[rule.pk]), {"category": "neutral"})
        rule.refresh_from_db()
        self.assertEqual(rule.category, "neutral")
