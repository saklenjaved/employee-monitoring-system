"""Email settings: the local .env file, SMTP through environment variables, and the console fallback."""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import Role
from accounts.services import open_invitation
from accounts.tests.factories import make_org, make_user
from config.settings import _load_env_file

SETTINGS = Path(__file__).resolve().parents[2] / "config" / "settings.py"
SMTP = "django.core.mail.backends.smtp.EmailBackend"


class EnvFileTests(SimpleTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / ".env"

    def load(self, text, env=None, argv=("manage.py", "runserver")):
        self.path.write_text(text, encoding="utf-8")
        with mock.patch.dict(os.environ, env or {}, clear=True), mock.patch.object(sys, "argv", list(argv)):
            _load_env_file(self.path)
            return dict(os.environ)

    def test_values_are_read_and_comments_and_quotes_handled(self):
        loaded = self.load("# a comment\n\nEMAIL_HOST=smtp.gmail.com\nEMAIL_HOST_PASSWORD=\"abcd efgh\"\n")
        self.assertEqual(loaded["EMAIL_HOST"], "smtp.gmail.com")
        self.assertEqual(loaded["EMAIL_HOST_PASSWORD"], "abcd efgh")

    def test_a_real_environment_variable_wins_over_the_file(self):
        loaded = self.load("EMAIL_HOST=smtp.gmail.com\n", env={"EMAIL_HOST": "smtp.other.test"})
        self.assertEqual(loaded["EMAIL_HOST"], "smtp.other.test")

    def test_the_file_is_ignored_during_tests(self):
        loaded = self.load("EMAIL_HOST=smtp.gmail.com\n", argv=("manage.py", "test"))
        self.assertNotIn("EMAIL_HOST", loaded)

    def test_the_file_is_ignored_when_the_skip_switch_is_set(self):
        loaded = self.load("EMAIL_HOST=smtp.gmail.com\n", env={"DJANGO_SKIP_ENV_FILE": "1"})
        self.assertNotIn("EMAIL_HOST", loaded)

    def test_a_missing_file_is_fine(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(sys, "argv", ["manage.py", "runserver"]):
            _load_env_file(self.path.with_name("missing.env"))
            self.assertEqual(dict(os.environ), {})


class BackendChoiceTests(SimpleTestCase):
    def load_settings(self, argv, env):
        full_env = {key: value for key, value in os.environ.items() if key not in ("DJANGO_DEBUG", "DJANGO_SECRET_KEY")}
        full_env.update(env)
        full_env["DJANGO_SKIP_ENV_FILE"] = "1"
        spec = importlib.util.spec_from_file_location("settings_email_under_test", SETTINGS)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, full_env, clear=True), mock.patch.object(sys, "argv", argv):
            spec.loader.exec_module(module)
        return module

    def test_development_with_an_smtp_host_sends_through_smtp(self):
        module = self.load_settings(["manage.py", "runserver"], {"EMAIL_HOST": "smtp.gmail.com"})
        self.assertEqual(module.EMAIL_BACKEND, SMTP)
        self.assertEqual(module.EMAIL_HOST, "smtp.gmail.com")
        self.assertEqual(module.EMAIL_PORT, 587)
        self.assertTrue(module.EMAIL_USE_TLS)

    def test_development_without_an_smtp_host_still_uses_smtp_and_never_prints_links(self):
        module = self.load_settings(["manage.py", "runserver"], {})
        self.assertEqual(module.EMAIL_BACKEND, SMTP)
        self.assertEqual(module.EMAIL_HOST, "")

    def test_production_always_uses_smtp(self):
        module = self.load_settings(["gunicorn", "app"], {"DJANGO_SECRET_KEY": "x" * 50})
        self.assertEqual(module.EMAIL_BACKEND, SMTP)

    def test_tls_can_be_switched_off_for_a_local_test_server(self):
        module = self.load_settings(["manage.py", "runserver"], {"EMAIL_HOST": "localhost", "EMAIL_USE_TLS": "0"})
        self.assertFalse(module.EMAIL_USE_TLS)


class SendFailureTests(TestCase):
    """When the mail server is missing or refuses, the app says so and never shows an error page."""

    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Acme")
        cls.admin = make_user("admin@acme.test", Role.ADMIN, cls.org)
        cls.employee, _, _ = open_invitation(cls.admin, cls.org, "new@acme.test", "New Person", Role.EMPLOYEE)

    def test_a_password_reset_still_gives_the_same_answer_when_sending_fails(self):
        with mock.patch("django.contrib.auth.forms.PasswordResetForm.save", side_effect=OSError("no server")):
            response = self.client.post(reverse("password_reset"), {"email": "new@acme.test"})
        self.assertRedirects(response, reverse("password_reset_done"))

    def test_an_invitation_resend_says_the_email_could_not_be_sent(self):
        self.client.force_login(self.admin)
        with mock.patch("accounts.services.send_mail", side_effect=OSError("no server")):
            response = self.client.post(reverse("person_resend_invite", args=[self.employee.pk]), follow=True)
        self.assertContains(response, "could not be sent")
