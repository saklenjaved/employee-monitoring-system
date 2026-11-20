import re

from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role, User
from accounts.tests.factories import make_membership
from organizations.models import Organization

PASSWORD = "a-long-test-password-1"


class LoginTests(TestCase):
    def setUp(self):
        cache.clear()  # login attempts are counted in the cache

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Org")
        cls.admin = User.objects.create_user(
            email="admin@org.test", password=PASSWORD, full_name="Admin", role=Role.ADMIN, organization=cls.org
        )
        make_membership(cls.admin)

    def test_login_with_correct_password(self):
        response = self.client.post(reverse("login"), {"username": "admin@org.test", "password": PASSWORD})
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)

    def test_login_email_is_case_insensitive(self):
        response = self.client.post(reverse("login"), {"username": "  Admin@Org.TEST ", "password": PASSWORD})
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)

    def test_login_with_wrong_password_fails(self):
        response = self.client.post(reverse("login"), {"username": "admin@org.test", "password": "wrong"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_deactivated_user_cannot_log_in(self):
        self.admin.is_active = False
        self.admin.save()
        response = self.client.post(reverse("login"), {"username": "admin@org.test", "password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_user_of_deactivated_organization_cannot_log_in(self):
        self.org.is_active = False
        self.org.save()
        response = self.client.post(reverse("login"), {"username": "admin@org.test", "password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "organization is deactivated")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_logout_requires_post_and_ends_session(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_home_needs_login(self):
        response = self.client.get(reverse("home"))
        self.assertRedirects(response, f"{reverse('login')}?next={reverse('home')}")

    def test_home_shows_role_and_organization(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Admin")
        self.assertContains(response, "Org")


class PasswordResetTests(TestCase):
    def setUp(self):
        cache.clear()  # reset requests are counted in the cache

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Org")
        cls.user = User.objects.create_user(
            email="employee@org.test", password=PASSWORD, full_name="E", role=Role.EMPLOYEE, organization=cls.org
        )
        make_membership(cls.user)

    def test_reset_email_is_sent_for_existing_account(self):
        response = self.client.post(reverse("password_reset"), {"email": "employee@org.test"})
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["employee@org.test"])

    def test_unknown_email_gets_same_response_and_no_email(self):
        response = self.client.post(reverse("password_reset"), {"email": "nobody@org.test"})
        self.assertRedirects(response, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)

    def test_full_reset_flow_sets_new_password_and_link_works_once(self):
        self.client.post(reverse("password_reset"), {"email": "employee@org.test"})
        link = re.search(r"http://testserver(/password-reset/[^\s]+)", mail.outbox[0].body).group(1)

        # Django swaps the token for a session value on first visit, then shows the form.
        response = self.client.get(link, follow=True)
        self.assertContains(response, "Choose your password")
        form_url = response.redirect_chain[-1][0]
        new_password = "another-long-password-2"
        response = self.client.post(form_url, {"new_password1": new_password, "new_password2": new_password})
        self.assertRedirects(response, reverse("password_reset_complete"))

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new_password))

        # The same link no longer works.
        response = self.client.get(link, follow=True)
        self.assertContains(response, "Link not valid")

    def test_weak_new_password_is_rejected(self):
        self.client.post(reverse("password_reset"), {"email": "employee@org.test"})
        link = re.search(r"http://testserver(/password-reset/[^\s]+)", mail.outbox[0].body).group(1)
        response = self.client.get(link, follow=True)
        form_url = response.redirect_chain[-1][0]
        response = self.client.post(form_url, {"new_password1": "12345678", "new_password2": "12345678"})
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))
