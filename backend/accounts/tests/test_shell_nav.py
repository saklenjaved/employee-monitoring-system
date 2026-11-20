"""The sidebar shows each role only its own links, and marks the current page."""
import re

from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import make_org, make_user


def highlighted_names(html):
    """Return the link titles that carry aria-current on this page."""
    return re.findall(r'<a href="[^"]*"[^>]*aria-current="page"[^>]*title="([^"]+)"', html)


class SidebarActiveTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org Active")
        cls.admin = make_user("admin@active.test", Role.ADMIN, cls.org)

    def test_each_page_highlights_only_its_own_sidebar_item(self):
        self.client.force_login(self.admin)
        cases = {
            "person_list": ["People"],
            "person_create": ["People"],
            "team_list": ["Teams"],
            "department_list": ["Departments"],
            "screenshot_list": ["Screenshots"],
            "screenshot_exclusions": ["Screenshot settings"],
            "rule_list": ["Rules"],
            "unclassified_list": ["Unclassified"],
            "deployment": ["Deployment"],
            "policy_settings": ["Monitoring settings"],
            "audit_log_list": ["Audit logs"],
        }
        for name, expected in cases.items():
            with self.subTest(page=name):
                response = self.client.get(reverse(name), follow=True)
                self.assertEqual(highlighted_names(response.content.decode()), expected)


class SidebarLinksTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org Nav")
        cls.admin = make_user("admin@nav.test", Role.ADMIN, cls.org)
        cls.manager = make_user("manager@nav.test", Role.MANAGER, cls.org)
        cls.emp = make_user("emp@nav.test", Role.EMPLOYEE, cls.org)

    def test_admin_sees_admin_links_and_current_page_is_marked(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard"), follow=True)
        self.assertContains(response, 'href="%s"' % reverse("person_list"))
        self.assertContains(response, 'href="%s"' % reverse("audit_log_list"))
        self.assertContains(response, 'aria-current="page"')

    def test_employee_does_not_see_admin_links(self):
        self.client.force_login(self.emp)
        response = self.client.get(reverse("dashboard"), follow=True)
        self.assertNotContains(response, 'href="%s"' % reverse("person_list"))
        self.assertNotContains(response, 'href="%s"' % reverse("policy_settings"))

    def test_manager_sees_screenshots_only(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("dashboard"), follow=True)
        self.assertContains(response, 'href="%s"' % reverse("screenshot_list"))
        self.assertNotContains(response, 'href="%s"' % reverse("person_list"))
