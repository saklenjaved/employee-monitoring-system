"""Each role lands on its own dashboard after sign-in, and cannot open another role's pages by typing the URL."""
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import PASSWORD, make_org, make_super_admin, make_user

# Pages that only one role may open. Everyone else gets 403.
# The audit log is open to both Admins and the Super Admin, so it is not listed here.
ROLE_PAGES = {
    Role.EMPLOYEE: ["dashboard_me"],
    Role.MANAGER: ["dashboard_teams"],
    Role.ADMIN: ["dashboard_organization", "person_list", "rule_list"],
    Role.SUPER_ADMIN: ["dashboard_platform", "organization_list"],
}
FIRST_PAGE = {
    Role.EMPLOYEE: "dashboard_me",
    Role.MANAGER: "dashboard_teams",
    Role.ADMIN: "dashboard_organization",
    Role.SUPER_ADMIN: "dashboard_platform",
}


class RoleLandingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Acme")
        cls.users = {
            Role.EMPLOYEE: make_user("e@acme.test", Role.EMPLOYEE, cls.org),
            Role.MANAGER: make_user("m@acme.test", Role.MANAGER, cls.org),
            Role.ADMIN: make_user("a@acme.test", Role.ADMIN, cls.org),
            Role.SUPER_ADMIN: make_super_admin(),
        }

    def test_sign_in_sends_each_role_to_its_own_dashboard(self):
        for role, user in self.users.items():
            with self.subTest(role=role):
                self.client.logout()
                response = self.client.post(
                    reverse("login"), {"username": user.email, "password": PASSWORD}, follow=True
                )
                self.assertEqual(response.resolver_match.url_name, FIRST_PAGE[role])

    def test_the_dashboard_link_opens_the_right_page_for_each_role(self):
        for role, user in self.users.items():
            with self.subTest(role=role):
                self.client.force_login(user)
                response = self.client.get(reverse("dashboard"), follow=True)
                self.assertEqual(response.resolver_match.url_name, FIRST_PAGE[role])
                self.assertEqual(response.status_code, 200)

    def test_each_role_can_open_its_own_pages(self):
        for role, url_names in ROLE_PAGES.items():
            for url_name in url_names:
                with self.subTest(role=role, page=url_name):
                    self.client.force_login(self.users[role])
                    self.assertNotEqual(self.client.get(reverse(url_name)).status_code, 403)

    def test_typed_urls_of_other_roles_are_forbidden(self):
        for role, user in self.users.items():
            for other_role, url_names in ROLE_PAGES.items():
                if other_role == role:
                    continue
                for url_name in url_names:
                    with self.subTest(role=role, page=url_name):
                        self.client.force_login(user)
                        self.assertEqual(self.client.get(reverse(url_name)).status_code, 403)
