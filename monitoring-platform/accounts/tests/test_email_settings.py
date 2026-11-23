"""Email settings: the local .env file, SMTP through environment variables, and the console fallback."""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
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

    def test_a_brevo_key_switches_mail_to_the_https_api(self):
        module = self.load_settings(["gunicorn", "app"], {"DJANGO_SECRET_KEY": "x" * 50, "BREVO_API_KEY": "key"})
        self.assertEqual(module.EMAIL_BACKEND, "config.brevo_email.BrevoEmailBackend")

    def test_mail_waits_at_most_ten_seconds_by_default(self):
        module = self.load_settings(["manage.py", "runserver"], {})
        self.assertEqual(module.EMAIL_TIMEOUT, 10)


class ProductionSettingsTests(SimpleTestCase):
    """What Render's environment variables turn into."""

    def load_production(self, extra):
        env = {key: value for key, value in os.environ.items() if key not in ("DJANGO_DEBUG", "DJANGO_SECRET_KEY")}
        env.update({"DJANGO_SECRET_KEY": "x" * 50, "DJANGO_SKIP_ENV_FILE": "1", **extra})
        spec = importlib.util.spec_from_file_location("settings_production_under_test", SETTINGS)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "argv", ["hypercorn", "app"]):
            spec.loader.exec_module(module)
        return module

    def test_database_url_gives_postgres_with_ssl_and_reused_connections(self):
        module = self.load_production({"DATABASE_URL": "postgres://user:pw@db.example.test:5432/app"})
        database = module.DATABASES["default"]
        self.assertEqual(database["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(database["HOST"], "db.example.test")
        self.assertEqual(database["OPTIONS"], {"sslmode": "require"})
        self.assertEqual(database["CONN_MAX_AGE"], 60)
        self.assertTrue(database["CONN_HEALTH_CHECKS"])

    def test_render_hostname_and_url_are_trusted_and_used_for_the_website_address(self):
        module = self.load_production(
            {"RENDER_EXTERNAL_HOSTNAME": "app.onrender.com", "RENDER_EXTERNAL_URL": "https://app.onrender.com"}
        )
        self.assertIn("app.onrender.com", module.ALLOWED_HOSTS)
        self.assertEqual(module.CSRF_TRUSTED_ORIGINS, ["https://app.onrender.com"])
        self.assertEqual(module.SITE_URL, "https://app.onrender.com")

    def test_production_forces_https_and_secure_cookies(self):
        module = self.load_production({})
        self.assertTrue(module.SECURE_SSL_REDIRECT)
        self.assertTrue(module.SESSION_COOKIE_SECURE)
        self.assertTrue(module.CSRF_COOKIE_SECURE)

    def test_production_refuses_to_start_without_a_secret_key(self):
        from django.core.exceptions import ImproperlyConfigured

        env = {key: value for key, value in os.environ.items() if key not in ("DJANGO_DEBUG", "DJANGO_SECRET_KEY")}
        env["DJANGO_SKIP_ENV_FILE"] = "1"
        spec = importlib.util.spec_from_file_location("settings_nokey_under_test", SETTINGS)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "argv", ["hypercorn", "app"]):
            with self.assertRaises(ImproperlyConfigured):
                spec.loader.exec_module(module)


class BrevoBackendTests(SimpleTestCase):
    def send(self, **overrides):
        from django.core.mail import EmailMultiAlternatives

        message = EmailMultiAlternatives("Subject", "Body text", "Monitoring <hello@acme.test>", ["new@acme.test"])
        message.attach_alternative("<p>Body</p>", "text/html")
        with override_settings(BREVO_API_KEY="secret-key", EMAIL_TIMEOUT=7, **overrides):
            from config.brevo_email import BrevoEmailBackend

            return BrevoEmailBackend().send_messages([message])

    def test_the_message_is_posted_to_brevo_over_https_with_a_timeout(self):
        import json

        with mock.patch("config.brevo_email.urllib.request.urlopen") as urlopen:
            sent = self.send()
        self.assertEqual(sent, 1)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.brevo.com/v3/smtp/email")
        self.assertEqual(request.get_header("Api-key"), "secret-key")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 7)
        body = json.loads(request.data)
        self.assertEqual(body["sender"], {"email": "hello@acme.test", "name": "Monitoring"})
        self.assertEqual(body["to"], [{"email": "new@acme.test"}])
        self.assertEqual((body["subject"], body["textContent"], body["htmlContent"]), ("Subject", "Body text", "<p>Body</p>"))
        self.assertNotIn("secret-key", request.data.decode())

    def refused(self, body, status=400):
        import io
        import urllib.error

        return urllib.error.HTTPError("https://api.brevo.com", status, "Error", {}, io.BytesIO(body))

    def test_a_refusal_is_logged_with_status_code_and_message_but_no_address_or_key(self):
        import urllib.error

        body = b'{"code": "invalid_parameter", "message": "Sender new@acme.test is not valid"}'
        with mock.patch("config.brevo_email.urllib.request.urlopen", side_effect=self.refused(body)):
            with self.assertLogs("config.brevo_email", level="WARNING") as logs:
                with self.assertRaises(urllib.error.HTTPError):
                    self.send()
        text = "\n".join(logs.output)
        self.assertIn("HTTP 400", text)
        self.assertIn("invalid_parameter", text)
        self.assertIn("Sender <email> is not valid", text)
        for secret in ("new@acme.test", "hello@acme.test", "secret-key", "Body text"):
            self.assertNotIn(secret, text)

    def test_a_long_refusal_message_is_shortened(self):
        import json
        import urllib.error

        body = json.dumps({"code": "bad", "message": "x" * 5000}).encode()
        with mock.patch("config.brevo_email.urllib.request.urlopen", side_effect=self.refused(body)):
            with self.assertLogs("config.brevo_email", level="WARNING") as logs:
                with self.assertRaises(urllib.error.HTTPError):
                    self.send()
        self.assertLess(len(logs.output[0]), 400)

    def test_a_refusal_that_is_not_json_is_logged_with_the_status_only(self):
        import urllib.error

        with mock.patch("config.brevo_email.urllib.request.urlopen", side_effect=self.refused(b"<html>secret page</html>", 401)):
            with self.assertLogs("config.brevo_email", level="WARNING") as logs:
                with self.assertRaises(urllib.error.HTTPError):
                    self.send()
        self.assertIn("HTTP 401", logs.output[0])
        self.assertNotIn("secret page", logs.output[0])

    def test_a_network_failure_is_logged_by_type_only(self):
        import urllib.error

        error = urllib.error.URLError(TimeoutError("api.brevo.com timed out for new@acme.test"))
        with mock.patch("config.brevo_email.urllib.request.urlopen", side_effect=error):
            with self.assertLogs("config.brevo_email", level="WARNING") as logs:
                with self.assertRaises(urllib.error.URLError):
                    self.send()
        self.assertIn("TimeoutError", logs.output[0])
        self.assertNotIn("new@acme.test", logs.output[0])

    def test_a_refused_message_raises_so_the_page_can_say_it_was_not_sent(self):
        import urllib.error

        error = urllib.error.HTTPError("https://api.brevo.com", 401, "Unauthorized", {}, None)
        with mock.patch("config.brevo_email.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(urllib.error.HTTPError):
                self.send()


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
