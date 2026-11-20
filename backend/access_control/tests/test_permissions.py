from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from accounts.tests.factories import make_membership
from organizations.models import Organization


def make_user(email, role, organization):
    user = User.objects.create_user(
        email=email, password="a-long-test-password-1", full_name=email, role=role, organization=organization
    )
    make_membership(user)
    return user


class RoleRequiredTests(TestCase):
    """Uses the organization list page, which is for the Super Admin only."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Org")
        cls.url = reverse("organization_list")
        cls.super_admin = User.objects.create_superuser(
            email="super@platform.test", password="a-long-test-password-1", full_name="Super"
        )
        cls.admin = make_user("admin@org.test", Role.ADMIN, cls.org)
        cls.manager = make_user("manager@org.test", Role.MANAGER, cls.org)
        cls.employee = make_user("employee@org.test", Role.EMPLOYEE, cls.org)

    def test_anonymous_user_is_sent_to_login(self):
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('login')}?next={self.url}")

    def test_super_admin_is_allowed(self):
        self.client.force_login(self.super_admin)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_other_roles_get_403(self):
        for user in (self.admin, self.manager, self.employee):
            with self.subTest(role=user.role):
                self.client.force_login(user)
                self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_user_of_deactivated_organization_is_logged_out(self):
        self.client.force_login(self.admin)
        self.org.is_active = False
        self.org.save()
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("login")))
        # The session was cleared, so the next request is anonymous.
        self.assertNotIn("_auth_user_id", self.client.session)
