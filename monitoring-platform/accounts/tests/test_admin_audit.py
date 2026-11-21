"""Changes made in the Django admin site must be audited, and nothing can be deleted there."""
import os
import sys
import importlib.util
from pathlib import Path
from unittest import mock

from django.contrib.admin.sites import site
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, SimpleTestCase, TestCase

from accounts.models import Role, User
from accounts.tests.factories import make_org, make_super_admin, make_user
from audit.models import AuditLog
from organizations.models import Organization


class FakeForm:
    changed_data = ["role", "organization"]


class AdminSiteAuditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org")
        cls.super_admin = make_super_admin()
        cls.employee = make_user("e@org.test", Role.EMPLOYEE, cls.org)

    def request(self):
        request = RequestFactory().post("/admin/")
        request.user = self.super_admin
        return request

    def test_changing_a_user_in_the_admin_is_audited(self):
        model_admin = site._registry[User]
        self.employee.role = Role.MANAGER
        model_admin.save_model(self.request(), self.employee, FakeForm(), change=True)
        entry = AuditLog.objects.get(action="user.changed_in_admin")
        self.assertEqual(entry.actor, self.super_admin)
        self.assertEqual(entry.target_id, str(self.employee.pk))
        self.assertEqual(entry.organization, self.org)
        self.assertEqual(entry.detail, "fields: organization, role")

    def test_changing_an_organization_in_the_admin_is_audited(self):
        model_admin = site._registry[Organization]
        self.org.is_active = False

        class Form:
            changed_data = ["is_active"]

        model_admin.save_model(self.request(), self.org, Form(), change=True)
        self.assertTrue(AuditLog.objects.filter(action="organization.changed_in_admin", organization=self.org).exists())

    def test_users_and_organizations_cannot_be_deleted_in_the_admin(self):
        for model in (User, Organization):
            self.assertFalse(site._registry[model].has_delete_permission(self.request()))


class DevelopmentSettingsTests(SimpleTestCase):
    """The development shortcuts must not switch on by accident."""

    SETTINGS = Path(__file__).resolve().parents[2] / "config" / "settings.py"

    def load_settings(self, argv, **extra_env):
        env = {key: value for key, value in os.environ.items() if key not in ("DJANGO_DEBUG", "DJANGO_SECRET_KEY")}
        env.update(extra_env)
        env["DJANGO_SKIP_ENV_FILE"] = "1"  # a local monitoring-platform/.env must not change these checks
        spec = importlib.util.spec_from_file_location("settings_under_test", self.SETTINGS)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "argv", argv):
            spec.loader.exec_module(module)
        return module

    def test_a_process_with_the_word_test_in_its_arguments_does_not_get_dev_mode(self):
        with self.assertRaises(ImproperlyConfigured):
            self.load_settings(["gunicorn", "--name", "test"])

    def test_manage_py_test_gets_dev_mode(self):
        module = self.load_settings(["manage.py", "test"])
        self.assertTrue(module.DEV_MODE)
        self.assertIn("MD5PasswordHasher", module.PASSWORD_HASHERS[0])

    def test_normal_commands_do_not_get_the_fast_hasher(self):
        module = self.load_settings(["manage.py", "migrate"], DJANGO_SECRET_KEY="x" * 50, DB_ENGINE="")
        self.assertFalse(hasattr(module, "PASSWORD_HASHERS"))
        self.assertTrue(module.SECURE_SSL_REDIRECT)

    def test_runserver_on_this_computer_starts_in_development_mode_without_setup(self):
        module = self.load_settings(["manage.py", "runserver"])
        self.assertTrue(module.DEBUG)
        self.assertTrue(module.DEV_MODE)

    def test_runserver_with_an_explicit_local_address_starts_in_development_mode(self):
        for address in ("127.0.0.1:8000", "localhost:8000", "8000"):
            with self.subTest(address=address):
                module = self.load_settings(["manage.py", "runserver", address])
                self.assertTrue(module.DEV_MODE)

    def test_runserver_on_other_addresses_still_needs_a_secret_key(self):
        for address in ("0.0.0.0:8000", "0.0.0.0", "192.168.1.5:8000", "example.com:80"):
            with self.subTest(address=address):
                with self.assertRaises(ImproperlyConfigured):
                    self.load_settings(["manage.py", "runserver", address])
                module = self.load_settings(["manage.py", "runserver", address], DJANGO_SECRET_KEY="x" * 50)
                self.assertFalse(module.DEBUG)
                self.assertFalse(module.DEV_MODE)
