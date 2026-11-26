"""The activation link from the invitation email pre-fills the email field. The link holds a signed ID, not the address."""
import re
from urllib.parse import unquote

from django.core import mail
from django.test import TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.services import invite_token, invited_email
from accounts.tests.factories import make_org, make_user
from accounts.tests.test_invitations import invite

LINK = re.compile(r"http://testserver/activate/\?invite=([^\s,]+)")


class ActivationPrefillTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Acme Prefill")
        cls.admin = make_user("owner-pre@acme.test", Role.ADMIN, cls.org)

    def setUp(self):
        mail.outbox = []
        invite(self.admin, self.org, "new-pre@acme.test", Role.EMPLOYEE)
        self.token = unquote(LINK.search(mail.outbox[0].body).group(1))  # the email link is URL-encoded

    def test_the_email_link_carries_a_token_not_the_address(self):
        self.assertNotIn("new-pre", self.token)
        self.assertNotIn("%40", self.token)

    def test_the_link_pre_fills_the_email_field(self):
        response = self.client.get(reverse("activate"), {"invite": self.token})
        self.assertContains(response, 'value="new-pre@acme.test"')

    def test_each_link_pre_fills_its_own_recipient(self):
        recipients = ["alice-pre@acme.test", "bob-pre@acme.test", "carol-pre@acme.test"]
        links = {}
        for email in recipients:
            mail.outbox = []
            invite(self.admin, self.org, email, Role.EMPLOYEE)
            links[email] = unquote(LINK.search(mail.outbox[0].body).group(1))
        for email in recipients:
            response = self.client.get(reverse("activate"), {"invite": links[email]})
            self.assertContains(response, f'value="{email}"')
            for other in recipients:
                if other != email:
                    self.assertNotContains(response, f'value="{other}"')

    def test_no_token_leaves_the_field_empty(self):
        response = self.client.get(reverse("activate"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "new-pre@acme.test")

    def test_a_tampered_token_pre_fills_nothing(self):
        response = self.client.get(reverse("activate"), {"invite": self.token + "x"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "new-pre@acme.test")
        self.assertEqual(invited_email("not-a-real-token"), "")

    def test_the_token_resolves_to_the_invited_account(self):
        self.assertEqual(invited_email(self.token), "new-pre@acme.test")
        self.assertEqual(invited_email(invite_token(self.admin)), "owner-pre@acme.test")

    def test_the_pre_filled_email_is_still_validated_and_editable(self):
        response = self.client.post(
            reverse("activate") + "?" + f"invite={self.token}",
            {"email": "not-an-email", "code": "12345678", "password": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a valid email address")

    def test_activation_still_needs_the_code(self):
        response = self.client.post(
            reverse("activate") + "?" + f"invite={self.token}",
            {"email": "new-pre@acme.test", "code": "00000000", "password": "Strong-pass-123"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="new-pre@acme.test"')
