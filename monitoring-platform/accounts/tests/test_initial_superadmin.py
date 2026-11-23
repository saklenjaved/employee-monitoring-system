"""The create_initial_superadmin command, which makes the first Super Admin on a host without a shell."""
import os
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase

from accounts.models import Role, User
from accounts.tests.factories import make_org, make_user

ENV = {"INITIAL_SUPERADMIN_EMAIL": "Boss@Example.test", "INITIAL_SUPERADMIN_PASSWORD": "a-Long-Safe-pass-42"}


class CreateInitialSuperadminTests(TestCase):
    def run_command(self, env=ENV):
        out = StringIO()
        with mock.patch.dict(os.environ, env, clear=False):
            call_command("create_initial_superadmin", stdout=out)
        return out.getvalue()

    def test_it_creates_a_super_admin_who_can_sign_in(self):
        output = self.run_command()
        user = User.objects.get(email="boss@example.test")
        self.assertEqual(user.role, Role.SUPER_ADMIN)
        self.assertTrue(user.is_staff and user.is_superuser and user.is_active)
        self.assertTrue(self.client.login(username="boss@example.test", password=ENV["INITIAL_SUPERADMIN_PASSWORD"]))
        self.assertNotIn(ENV["INITIAL_SUPERADMIN_PASSWORD"], output)  # the password is never printed

    def test_running_it_again_does_not_make_a_second_super_admin(self):
        self.run_command()
        output = self.run_command({**ENV, "INITIAL_SUPERADMIN_EMAIL": "other@example.test"})
        self.assertEqual(User.objects.filter(role=Role.SUPER_ADMIN).count(), 1)
        self.assertIn("already exists", output)

    def test_without_the_variables_it_does_nothing(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in ENV:
                os.environ.pop(name, None)
            out = StringIO()
            call_command("create_initial_superadmin", stdout=out)
        self.assertFalse(User.objects.exists())

    def test_an_email_that_already_has_an_account_is_skipped_without_failing(self):
        make_user("boss@example.test", Role.ADMIN, make_org("Acme"))
        output = self.run_command()
        self.assertIn("skipped", output)
        self.assertEqual(User.objects.get(email="boss@example.test").role, Role.ADMIN)

    def test_a_weak_password_is_skipped_without_failing(self):
        output = self.run_command({**ENV, "INITIAL_SUPERADMIN_PASSWORD": "short"})
        self.assertIn("skipped", output)
        self.assertFalse(User.objects.exists())
