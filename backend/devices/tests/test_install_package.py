"""The one-click install package an employee downloads while an Admin's setup window is open."""
import re
import tempfile
from datetime import timedelta
from pathlib import Path

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from audit.models import AuditLog
from devices.models import PairingRequest, SetupWindow
from devices.pairing import issue_install_token, open_setup_window, organization_for_token

PC = {"device_name": "LAPTOP-7", "os_version": "Windows 11", "agent_version": "0.1.0"}


class InstallPackageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)
        cls.emp = make_user("e@a.test", Role.EMPLOYEE, cls.org, "Employee A")
        cls.other = make_user("o@a.test", Role.EMPLOYEE, cls.org, "Other")

    def setUp(self):
        cache.clear()
        self.folder = tempfile.TemporaryDirectory()
        self.installer = Path(self.folder.name) / "WinAgentSetup.exe"
        self.installer.write_bytes(b"fake installer")
        self.addCleanup(self.folder.cleanup)

    def download(self, secure=True, consent="yes"):
        data = {"consent": consent} if consent else {}
        response = self.client.post(reverse("install_package"), data, secure=secure)
        self.addCleanup(response.close)  # the open file must close before the temp folder is removed (Windows)
        return response

    def with_installer(self):
        return override_settings(AGENT_INSTALLER_PATH=str(self.installer))

    def file_name(self, response):
        return re.search(r'filename="?([^";]+)', response["Content-Disposition"]).group(1)

    def test_an_employee_can_download_without_an_admin_window(self):
        self.client.force_login(self.emp)
        with self.with_installer(), override_settings(AGENT_BASE_URL="https://agents.example.test/api/agent/v1"):
            response = self.download()
        self.assertEqual(response.status_code, 200)

    def test_an_open_window_gives_one_installer_file_with_the_token_in_its_name(self):
        open_setup_window(self.admin, self.emp)
        self.client.force_login(self.emp)
        with self.with_installer(), override_settings(AGENT_BASE_URL="https://agents.example.test/api/agent/v1"):
            response = self.download()
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response["Content-Type"], "application/zip")
        self.assertIn("no-store", response["Cache-Control"])
        name = self.file_name(response)
        self.assertTrue(name.startswith("WinAgentSetup-") and name.endswith(".exe"))
        self.assertEqual(b"".join(response.streaming_content), b"fake installer")
        self.assertEqual(organization_for_token(name[len("WinAgentSetup-"):-4]), self.org)

    def test_in_development_the_dashboard_may_be_on_http(self):
        open_setup_window(self.admin, self.emp)
        self.client.force_login(self.emp)
        with self.with_installer(), override_settings(DEV_MODE=True, AGENT_BASE_URL="https://127.0.0.1:8443/api/agent/v1"):
            response = self.download(secure=False)
        self.assertEqual(response.status_code, 200)

    def test_outside_development_the_download_needs_an_https_dashboard(self):
        window, _ = open_setup_window(self.admin, self.emp)
        self.client.force_login(self.emp)
        with self.with_installer(), override_settings(DEV_MODE=False, AGENT_BASE_URL="https://agents.example.test/api/agent/v1"):
            response = self.download(secure=False)
        self.assertRedirects(response, reverse("dashboard_me"))
        window.refresh_from_db()
        self.assertEqual(window.install_token_hash, "")

    def test_an_agent_address_that_is_not_https_is_refused(self):
        window, _ = open_setup_window(self.admin, self.emp)
        self.client.force_login(self.emp)
        with self.with_installer(), override_settings(AGENT_BASE_URL="http://agents.example.test/api/agent/v1"):
            response = self.download()
        self.assertRedirects(response, reverse("dashboard_me"))
        window.refresh_from_db()
        self.assertEqual(window.install_token_hash, "")

    def test_with_no_agent_address_an_http_dashboard_cannot_build_one(self):
        open_setup_window(self.admin, self.emp)
        self.client.force_login(self.emp)
        with self.with_installer(), override_settings(DEV_MODE=True, AGENT_BASE_URL=""):
            response = self.download(secure=False)
        self.assertRedirects(response, reverse("dashboard_me"))

    def test_no_download_without_the_employees_consent(self):
        window, _ = open_setup_window(self.admin, self.emp)
        self.client.force_login(self.emp)
        with self.with_installer(), override_settings(AGENT_BASE_URL="https://agents.example.test/api/agent/v1"):
            response = self.download(consent=None)
        self.assertRedirects(response, reverse("dashboard_me"))
        window.refresh_from_db()
        self.assertEqual(window.install_token_hash, "")

    def test_a_missing_installer_is_reported_not_crashed(self):
        open_setup_window(self.admin, self.emp)
        self.client.force_login(self.emp)
        with override_settings(AGENT_INSTALLER_PATH=str(Path(self.folder.name) / "missing.exe")):
            response = self.download()
        self.assertRedirects(response, reverse("dashboard_me"))

    def test_only_the_employee_with_the_open_window_can_download(self):
        open_setup_window(self.admin, self.emp)
        self.client.force_login(self.other)
        with self.with_installer():
            response = self.download()
        self.assertRedirects(response, reverse("dashboard_me"))
        self.client.force_login(self.admin)
        self.assertEqual(self.download().status_code, 403)

    def test_the_token_is_stored_only_as_a_hash_and_never_audited(self):
        window, _ = open_setup_window(self.admin, self.emp)
        self.client.force_login(self.emp)
        with self.with_installer(), override_settings(AGENT_BASE_URL="https://agents.example.test/api/agent/v1"):
            response = self.download()
        token = self.file_name(response)[len("WinAgentSetup-"):-4]
        window.refresh_from_db()
        self.assertNotEqual(window.install_token_hash, token)
        for entry in AuditLog.objects.all():
            self.assertNotIn(token, f"{entry.action}{entry.detail}")

    def test_the_download_is_audited(self):
        open_setup_window(self.admin, self.emp)
        self.client.force_login(self.emp)
        with self.with_installer(), override_settings(AGENT_BASE_URL="https://agents.example.test/api/agent/v1"):
            self.download()
        self.assertTrue(AuditLog.objects.filter(action="setup.install_package_downloaded", actor=self.emp).exists())

    def test_the_dashboard_shows_the_download_until_a_pc_is_connected(self):
        self.client.force_login(self.emp)
        page = self.client.get(reverse("dashboard_me"))
        self.assertContains(page, "Download WinAgent setup")
        self.assertContains(page, "I agree to install WinAgent")


class InstallTokenTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org A")
        cls.org_b = make_org("Org B")
        cls.admin = make_user("admin@a.test", Role.ADMIN, cls.org)
        cls.emp = make_user("e@a.test", Role.EMPLOYEE, cls.org)

    def setUp(self):
        cache.clear()

    def test_the_token_identifies_the_organization_while_the_window_is_open(self):
        window, _ = open_setup_window(self.admin, self.emp)
        token = issue_install_token(window)
        self.assertEqual(organization_for_token(token), self.org)

    def test_a_new_download_replaces_the_old_token(self):
        window, _ = open_setup_window(self.admin, self.emp)
        old = issue_install_token(window)
        issue_install_token(window)
        self.assertIsNone(organization_for_token(old))

    def test_the_token_survives_closed_and_newer_windows_until_a_new_download(self):
        window, _ = open_setup_window(self.admin, self.emp)
        token = issue_install_token(window)
        SetupWindow.objects.filter(pk=window.pk).update(closed_at=timezone.now())
        self.assertEqual(organization_for_token(token), self.org)
        newer, _ = open_setup_window(self.admin, self.emp)
        self.assertEqual(organization_for_token(token), self.org)
        new_token = issue_install_token(newer)
        self.assertIsNone(organization_for_token(token))
        self.assertEqual(organization_for_token(new_token), self.org)

    def test_the_token_stops_working_when_the_employee_is_deactivated(self):
        window, _ = open_setup_window(self.admin, self.emp)
        token = issue_install_token(window)
        self.emp.is_active = False
        self.emp.save(update_fields=["is_active"])
        self.assertIsNone(organization_for_token(token))

    def test_the_pairing_api_accepts_the_install_token_and_attaches_the_organization(self):
        window, _ = open_setup_window(self.admin, self.emp)
        token = issue_install_token(window)
        response = APIClient().post("/api/agent/v1/pairing/requests/", {**PC, "org_token": token}, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(PairingRequest.objects.get().organization, self.org)
