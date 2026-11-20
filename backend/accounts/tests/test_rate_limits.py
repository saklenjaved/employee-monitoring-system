from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import PASSWORD, make_org, make_user
from accounts.views import (
    LOGIN_FAILURES_PER_EMAIL,
    RESET_REQUESTS_PER_EMAIL,
    RESET_REQUESTS_PER_IP,
)


class LoginRateLimitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        org = make_org("Org")
        make_user("admin@org.test", Role.ADMIN, org)
        make_user("other@org.test", Role.ADMIN, org)

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def fail(self, email="admin@org.test"):
        return self.client.post(reverse("login"), {"username": email, "password": "wrong"})

    def test_repeated_failures_lock_the_email_even_for_the_right_password(self):
        for _ in range(LOGIN_FAILURES_PER_EMAIL):
            self.assertEqual(self.fail().status_code, 200)
        locked = self.client.post(reverse("login"), {"username": "admin@org.test", "password": PASSWORD})
        self.assertEqual(locked.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_the_lock_ignores_letter_case_of_the_email(self):
        for _ in range(LOGIN_FAILURES_PER_EMAIL):
            self.fail("Admin@Org.test")
        self.assertEqual(self.fail("admin@org.test").status_code, 429)

    def test_other_emails_are_not_locked(self):
        for _ in range(LOGIN_FAILURES_PER_EMAIL):
            self.fail()
        response = self.client.post(reverse("login"), {"username": "other@org.test", "password": PASSWORD})
        self.assertEqual(response.status_code, 302)

    def test_a_successful_login_resets_the_counter(self):
        for _ in range(LOGIN_FAILURES_PER_EMAIL - 1):
            self.fail()
        ok = self.client.post(reverse("login"), {"username": "admin@org.test", "password": PASSWORD})
        self.assertEqual(ok.status_code, 302)
        self.client.logout()
        for _ in range(LOGIN_FAILURES_PER_EMAIL - 1):
            self.assertEqual(self.fail().status_code, 200)


class PasswordResetRateLimitTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        org = make_org("Org")
        make_user("e@org.test", Role.EMPLOYEE, org)

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_an_address_gets_only_a_few_emails_but_the_same_answer(self):
        responses = []
        for _ in range(RESET_REQUESTS_PER_EMAIL + 2):
            responses.append(self.client.post(reverse("password_reset"), {"email": "e@org.test"}))
        self.assertTrue(all(r.status_code == 302 and r.url == reverse("password_reset_done") for r in responses))
        self.assertEqual(len(mail.outbox), RESET_REQUESTS_PER_EMAIL)

    def test_one_ip_address_is_limited(self):
        for i in range(RESET_REQUESTS_PER_IP):
            self.client.post(reverse("password_reset"), {"email": f"nobody{i}@org.test"})
        response = self.client.post(reverse("password_reset"), {"email": "nobody@org.test"})
        self.assertEqual(response.status_code, 429)
