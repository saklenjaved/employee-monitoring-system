"""After activation, Employees land on their Employee dashboard and Admins on the Admin dashboard.

The activation page itself never shows the app sidebar, even to someone who is already signed in.
"""
import re
from urllib.parse import unquote

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from accounts.tests.test_invitations import invite

LINK = re.compile(r"http://testserver/activate/\?invite=([^\s,]+)")
CODE = re.compile(r"^\s{4}(\d{8})\s*$", re.MULTILINE)
PASSWORD = "Strong-pass-123"
SHELL = 'aria-label="Main navigation"'


class ActivationSignInBase(TestCase):
    def invite_and_read(self, role, email):
        mail.outbox = []
        invite(self.owner, self.org, email, role)
        body = mail.outbox[-1].body
        return unquote(LINK.search(body).group(1)), CODE.search(body).group(1)

    def activate(self, email, code, password=PASSWORD):
        return self.client.post(
            reverse("activate"),
            {"email": email, "code": code, "password": password, "password_confirm": password},
        )


class EmployeeActivationTests(ActivationSignInBase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Employee Flow Co")
        cls.owner = make_user("owner-emp@flow.test", Role.ADMIN, cls.org)

    def test_employee_is_signed_in_and_sent_to_the_employee_dashboard(self):
        email = "staff-emp@flow.test"
        _, code = self.invite_and_read(Role.EMPLOYEE, email)
        response = self.activate(email, code)
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        user = get_user_model().objects.get(email=email)
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))
        final = self.client.get(reverse("dashboard"), follow=True)
        self.assertEqual(final.redirect_chain[-1][0], reverse("dashboard_me"))

    def test_the_employee_activation_page_has_no_sidebar_when_signed_in(self):
        email = "signed-emp@flow.test"
        _, code = self.invite_and_read(Role.EMPLOYEE, email)
        self.client.force_login(self.owner)  # an Admin's browser opens the link
        response = self.client.get(reverse("activate"), {"invite": "x"})
        self.assertNotContains(response, SHELL)
        self.assertContains(response, "Activate your account")


class AdminActivationTests(ActivationSignInBase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Admin Flow Co")
        cls.owner = make_user("owner-adm@flow.test", Role.ADMIN, cls.org)

    def test_admin_is_signed_in_and_sent_to_the_admin_dashboard(self):
        email = "new-adm@flow.test"
        _, code = self.invite_and_read(Role.ADMIN, email)
        response = self.activate(email, code)
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        final = self.client.get(reverse("dashboard"), follow=True)
        self.assertEqual(final.redirect_chain[-1][0], reverse("dashboard_organization"))

    def test_a_returning_account_keeps_its_password_and_is_not_signed_in_by_a_guess(self):
        # A returning person of this organization keeps their own password. A wrong password must not sign them in.
        email = "old-adm@flow.test"
        returning = make_user(email, Role.ADMIN, self.org)
        returning.set_password("Their-own-password-1")
        returning.save()
        returning.memberships.update(status="ended", end_reason="removed")
        _, code = self.invite_and_read(Role.ADMIN, email)
        response = self.activate(email, code, password="Guessed-password-9")
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
