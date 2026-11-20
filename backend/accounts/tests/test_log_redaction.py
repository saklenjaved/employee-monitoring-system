"""Password-reset tokens must never reach the request logs, and the links must still work."""
import logging
import re

from django.core import mail
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.log_redaction import redact
from accounts.models import Role
from accounts.tests.factories import make_org, make_user

LINK = re.compile(r"http://testserver(/password-reset/[^\s]+)")


class RedactTests(SimpleTestCase):
    def test_the_token_is_replaced_in_a_request_line(self):
        line = '"GET /password-reset/MQ/c3f1-abc123-token/ HTTP/1.1" 302 0'
        self.assertEqual(redact(line), '"GET /password-reset/MQ/<redacted>/ HTTP/1.1" 302 0')

    def test_the_token_is_replaced_in_a_not_found_message(self):
        self.assertEqual(
            redact("Not Found: /password-reset/MQ/c3f1-abc123-token/"),
            "Not Found: /password-reset/MQ/<redacted>/",
        )

    def test_the_set_password_step_is_left_alone(self):
        line = '"GET /password-reset/MQ/set-password/ HTTP/1.1" 200 0'
        self.assertEqual(redact(line), line)

    def test_other_pages_are_left_alone(self):
        line = '"GET /workforce/people/3/ HTTP/1.1" 200 0'
        self.assertEqual(redact(line), line)


class RedactionInLogsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Acme")
        cls.user = make_user("reset@acme.test", Role.EMPLOYEE, cls.org)

    def setUp(self):
        cache.clear()
        mail.outbox = []

    def reset_link(self):
        self.client.post(reverse("password_reset"), {"email": "reset@acme.test"})
        path = LINK.search(mail.outbox[0].body).group(1)
        uid, token = path.strip("/").split("/")[1:3]
        return path, uid, token

    def test_a_request_log_line_for_the_real_link_has_no_token(self):
        path, _, token = self.reset_link()
        logger = logging.getLogger("django.server")
        with self.assertLogs("django.server", level="INFO") as captured:
            logger.info('"%s" %s %s', f"GET {path} HTTP/1.1", "302", "0")
        self.assertNotIn(token, "\n".join(captured.output))

    def test_a_not_found_log_for_a_tampered_link_has_no_token(self):
        path, _, token = self.reset_link()
        with self.assertLogs("django.request", level="WARNING") as captured:
            self.client.get(f"{path}extra/")
        self.assertIn("Not Found", "\n".join(captured.output))
        self.assertNotIn(token, "\n".join(captured.output))

    def test_the_link_still_works_and_moves_to_the_set_password_step(self):
        path, uid, _ = self.reset_link()
        response = self.client.get(path)
        self.assertRedirects(
            response, reverse("password_reset_confirm", args=[uid, "set-password"]), fetch_redirect_response=False
        )
        # The token is kept in the session after the first click, so the step page is still valid.
        self.assertEqual(self.client.get(response.url).status_code, 200)
