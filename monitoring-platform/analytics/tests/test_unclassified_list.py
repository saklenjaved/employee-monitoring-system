"""The Unclassified page: search, type filter, sorting and empty states. Limited to the Admin's organization."""
from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from analytics.models import ItemUsageDaily
from productivity.models import ClassificationRule
from productivity.services import create_rule


def names_on_page(response):
    return [item["name"] for item in response.context["listing"]["page"]]


class UnclassifiedListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.employee_a = make_user("e@a.test", Role.EMPLOYEE, cls.org_a)
        cls.employee_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b)
        day = date.today() - timedelta(days=1)
        usage = (
            (cls.employee_a, "app", "mystery.exe", 90),
            (cls.employee_a, "app", "other.exe", 500),
            (cls.employee_a, "website", "news.test", 200),
            (cls.employee_b, "app", "secret-b.exe", 999),
        )
        for user, item_type, name, seconds in usage:
            ItemUsageDaily.objects.create(
                organization=user.organization, user=user, date=day, item_type=item_type, name=name, seconds=seconds
            )
        cls.url = reverse("unclassified_list")

    def setUp(self):
        self.client.force_login(self.admin_a)

    def get(self, query_string=""):
        return self.client.get(f"{self.url}?{query_string}")

    def test_busiest_items_come_first(self):
        self.assertEqual(names_on_page(self.get()), ["other.exe", "news.test", "mystery.exe"])

    def test_another_organizations_item_is_never_listed(self):
        response = self.get()
        self.assertNotContains(response, "secret-b.exe")
        self.assertNotContains(self.get("q=secret"), "secret-b.exe")
        self.assertContains(self.get("q=secret"), "No items match your search or filters.")

    def test_search_by_name(self):
        self.assertEqual(names_on_page(self.get("q=MYSTERY")), ["mystery.exe"])

    def test_type_filter(self):
        self.assertEqual(names_on_page(self.get("item_type=website")), ["news.test"])
        self.assertEqual(names_on_page(self.get("item_type=app")), ["other.exe", "mystery.exe"])

    def test_sort_by_name(self):
        self.assertEqual(names_on_page(self.get("sort=name&dir=asc")), ["mystery.exe", "news.test", "other.exe"])

    def test_the_page_says_how_many_items_it_can_list(self):
        self.assertContains(self.get(), "Up to 1,000 are listed")

    def test_everything_classified_says_so(self):
        create_rule(self.admin_a, self.org_a, "app", "mystery.exe", "neutral")
        create_rule(self.admin_a, self.org_a, "app", "other.exe", "neutral")
        create_rule(self.admin_a, self.org_a, "website", "news.test", "neutral")
        response = self.get()
        self.assertContains(response, "Everything is classified")
        self.assertNotContains(response, "Clear search and filters")


class InPlaceClassifyTests(UnclassifiedListTests):
    """The Save button sends HTMX requests: the row is removed on success and kept on failure."""

    def post(self, **data):
        payload = {"item_type": "app", "name": "mystery.exe", "category": "productive", **data}
        return self.client.post(reverse("unclassified_classify"), payload, HTTP_HX_REQUEST="true")

    def test_a_saved_item_removes_its_row_and_refreshes_the_list(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        trigger = response.headers["HX-Trigger"]
        self.assertIn('"tags": "success"', trigger)
        self.assertIn('"refreshList": true', trigger)
        self.assertNotIn("HX-Reswap", response.headers)

    def test_a_failed_save_keeps_the_row_and_says_why(self):
        response = self.post(category="not-a-category")
        self.assertEqual(response.headers["HX-Reswap"], "none")
        self.assertIn('"tags": "error"', response.headers["HX-Trigger"])
        self.assertNotIn("refreshList", response.headers["HX-Trigger"])

    def test_an_already_classified_item_is_not_saved_twice(self):
        self.post()
        response = self.post()
        self.assertIn('"tags": "error"', response.headers["HX-Trigger"])
        self.assertEqual(response.headers["HX-Reswap"], "none")
        self.assertEqual(ClassificationRule.objects.filter(organization=self.org_a, pattern="mystery.exe").count(), 1)

    def test_a_normal_form_post_still_redirects_with_a_message(self):
        response = self.client.post(
            reverse("unclassified_classify"), {"item_type": "app", "name": "other.exe", "category": "neutral"}
        )
        self.assertRedirects(response, self.url)
