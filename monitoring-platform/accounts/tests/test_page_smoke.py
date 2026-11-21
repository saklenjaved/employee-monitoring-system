"""Every main page loads for the role that should see it, and employees cannot open admin pages."""
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import make_org, make_user

ADMIN_PAGES = [
    "person_list", "person_create", "department_list", "team_list", "rule_list", "unclassified_list",
    "screenshot_list", "screenshot_settings", "screenshot_exclusions", "deployment", "policy_settings",
    "audit_log_list",
]
SHARED_PAGES = ["home", "profile", "password_change"]


class PageSmokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)
        cls.emp = make_user("e@a.test", Role.EMPLOYEE, cls.org)

    def test_admin_pages_load_for_an_admin(self):
        self.client.force_login(self.admin)
        for name in ADMIN_PAGES + SHARED_PAGES:
            with self.subTest(page=name):
                response = self.client.get(reverse(name), follow=True)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "Traceback", status_code=200)

    def test_employee_pages_load_for_an_employee(self):
        self.client.force_login(self.emp)
        for name in SHARED_PAGES + ["dashboard_me"]:
            with self.subTest(page=name):
                response = self.client.get(reverse(name), follow=True)
                self.assertEqual(response.status_code, 200)

    def test_employees_cannot_open_admin_pages(self):
        self.client.force_login(self.emp)
        for name in ADMIN_PAGES:
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertNotEqual(response.status_code, 200)
