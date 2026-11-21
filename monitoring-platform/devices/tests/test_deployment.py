"""The Admin Deployment page: installer token, install command and the IT script."""
import tempfile
from pathlib import Path

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from audit.models import AuditLog
from datetime import timedelta

from django.utils import timezone

from devices.pairing import open_setup_window, organization_for_token
from devices.tests.test_setup import make_device


class DeploymentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.admin_b = make_user("admin@b.test", Role.ADMIN, cls.org_b)
        cls.manager_a = make_user("m@a.test", Role.MANAGER, cls.org_a)
        cls.emp_a = make_user("e@a.test", Role.EMPLOYEE, cls.org_a)

    def setUp(self):
        cache.clear()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.installer = Path(folder.name) / "WinAgentSetup-0.1.0.exe"
        self.installer.write_bytes(b"x")

    def test_the_admin_page_shows_the_command_without_any_token_at_first(self):
        self.client.force_login(self.admin_a)
        with override_settings(AGENT_INSTALLER_PATH=str(self.installer), AGENT_BASE_URL=""):
            response = self.client.get(reverse("deployment"), secure=True)
        self.assertContains(response, "/APIURL=https://testserver/api/agent/v1")
        self.assertContains(response, "No token yet.")
        self.assertNotContains(response, "The installer file is not on this server")

    def test_a_missing_installer_is_flagged(self):
        self.client.force_login(self.admin_a)
        with override_settings(AGENT_INSTALLER_PATH=str(self.installer.with_name("missing.exe"))):
            response = self.client.get(reverse("deployment"))
        self.assertContains(response, "The installer file is not on this server")

    def test_making_a_token_shows_it_once_and_it_works_for_this_organization(self):
        self.client.force_login(self.admin_a)
        response = self.client.post(reverse("deployment_token_new"))
        token = response.context["token"]
        self.assertContains(response, token)
        self.assertEqual(organization_for_token(token), self.org_a)
        self.assertNotContains(self.client.get(reverse("deployment")), token)
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_the_token_is_audited_without_its_value(self):
        self.client.force_login(self.admin_a)
        token = self.client.post(reverse("deployment_token_new")).context["token"]
        entry = AuditLog.objects.get(action="enrollment_token.issued")
        self.assertNotIn(token, f"{entry.detail}{entry.actor_email}")

    def test_a_new_token_stops_the_old_one_and_stays_inside_the_organization(self):
        self.client.force_login(self.admin_a)
        old = self.client.post(reverse("deployment_token_new")).context["token"]
        new = self.client.post(reverse("deployment_token_new")).context["token"]
        self.assertIsNone(organization_for_token(old))
        self.assertEqual(organization_for_token(new), self.org_a)
        self.client.force_login(self.admin_b)
        other = self.client.post(reverse("deployment_token_new")).context["token"]
        self.assertEqual(organization_for_token(other), self.org_b)
        self.assertEqual(organization_for_token(new), self.org_a)

    def test_the_script_has_no_secret_and_takes_the_token_as_a_parameter(self):
        self.client.force_login(self.admin_a)
        with override_settings(AGENT_INSTALLER_PATH=str(self.installer), AGENT_BASE_URL=""):
            response = self.client.get(reverse("deployment_script"))
        body = response.content.decode()
        self.assertIn("/APIURL=http://testserver/api/agent/v1", body)
        self.assertIn("[Parameter(Mandatory = $true)][string]$OrgToken", body)
        self.assertIn("WinAgentSetup-0.1.0.exe", body)
        self.assertIn("Deploy-WinAgent.ps1", response["Content-Disposition"])
        # Screenshots stay off on the PC unless IT asks for them.
        self.assertIn("[switch]$Screenshots", body)
        self.assertIn("if ($Screenshots) { $installArgs += '/SCREENSHOTS=1' }", body)

    def test_the_admin_can_download_the_built_installer(self):
        self.client.force_login(self.admin_a)
        with override_settings(AGENT_INSTALLER_PATH=str(self.installer), AGENT_BASE_URL=""):
            response = self.client.get(reverse("deployment_installer"))
            page = self.client.get(reverse("deployment"))
            self.assertEqual(b"".join(response.streaming_content), b"x")
        self.assertIn("WinAgentSetup-0.1.0.exe", response["Content-Disposition"])
        self.assertContains(page, reverse("deployment_installer"))

    def test_no_installer_file_means_not_found_and_no_download_button(self):
        self.client.force_login(self.admin_a)
        with override_settings(AGENT_INSTALLER_PATH=str(self.installer.with_name("missing.exe"))):
            self.assertEqual(self.client.get(reverse("deployment_installer")).status_code, 404)
            self.assertNotContains(self.client.get(reverse("deployment")), reverse("deployment_installer"))

    def test_the_default_installer_path_is_where_the_build_script_copies_it(self):
        from django.conf import settings

        path = Path(settings.AGENT_INSTALLER_PATH)
        self.assertEqual((path.parent.name, path.name), ("agent_dist", "WinAgentSetup.exe"))

    def test_only_admins_can_open_any_deployment_page(self):
        for user in (self.manager_a, self.emp_a):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("deployment_installer")).status_code, 403)
            for name in ("deployment", "deployment_script"):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403, (user.email, name))
            self.assertEqual(self.client.post(reverse("deployment_token_new")).status_code, 403)

    def test_the_configured_agent_address_wins_on_the_page_and_in_the_script(self):
        self.client.force_login(self.admin_a)
        with override_settings(AGENT_INSTALLER_PATH=str(self.installer), AGENT_BASE_URL="https://agents.example.test/api/agent/v1"):
            page = self.client.get(reverse("deployment"))
            script = self.client.get(reverse("deployment_script")).content.decode()
        self.assertContains(page, "/APIURL=https://agents.example.test/api/agent/v1")
        self.assertIn("/APIURL=https://agents.example.test/api/agent/v1", script)


class DeploymentStagesTests(TestCase):
    """Each employee's stage and next action come from the real records, never from a fixed value."""

    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Stage Org")
        cls.admin = make_user("admin@stage.test", Role.ADMIN, cls.org)
        cls.emp = make_user("emp@stage.test", Role.EMPLOYEE, cls.org)
        cls.other_org_emp = make_user("other@stage.test", Role.EMPLOYEE, make_org("Elsewhere"))

    def setUp(self):
        self.client.force_login(self.admin)

    def row_for(self, person):
        response = self.client.get(reverse("deployment"), secure=True)
        return next(r for r in response.context["rows"] if r["pk"] == person.pk)

    def test_an_employee_with_no_setup_link_is_waiting_for_installation(self):
        row = self.row_for(self.emp)
        self.assertEqual(row["stage"], "waiting_install")
        self.assertTrue(row["send_link"])
        self.assertFalse(row["resend_link"])

    def test_after_a_setup_link_is_sent_the_row_offers_to_resend_it(self):
        open_setup_window(self.admin, self.emp)
        row = self.row_for(self.emp)
        self.assertEqual(row["stage"], "waiting_install")
        self.assertFalse(row["send_link"])
        self.assertTrue(row["resend_link"])

    def test_a_pc_asking_to_connect_waits_for_the_employee_approval(self):
        from devices.models import PairingRequest

        open_setup_window(self.admin, self.emp)
        PairingRequest.objects.create(
            poll_token_hash="x" * 64,
            device_name="Waiting PC",
            os_version="Windows 11",
            agent_version="0.1.0",
            organization=self.org,
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        row = self.row_for(self.emp)
        self.assertEqual(row["stage"], "pairing_pending")
        self.assertEqual(row["install_label"], "Installed")
        self.assertFalse(row["send_link"])

    def test_a_recently_seen_device_is_connected_and_can_be_started(self):
        make_device(self.emp, last_seen=timezone.now())
        row = self.row_for(self.emp)
        self.assertEqual(row["stage"], "connected")
        self.assertTrue(row["can_start"])
        self.assertFalse(row["monitoring_active"])

    def test_a_device_that_was_never_seen_is_offline_when_nothing_is_connecting(self):
        make_device(self.emp, last_seen=None)
        self.assertEqual(self.row_for(self.emp)["stage"], "offline")

    def test_an_agent_error_is_a_connection_failure(self):
        device = make_device(self.emp, last_seen=timezone.now())
        device.last_error_code = "credential_rejected"
        device.save()
        row = self.row_for(self.emp)
        self.assertEqual(row["stage"], "failed")
        self.assertFalse(row["can_start"])

    def test_employees_of_other_organizations_never_appear(self):
        response = self.client.get(reverse("deployment"), secure=True)
        self.assertNotIn(self.other_org_emp.pk, [r["pk"] for r in response.context["rows"]])

    def test_the_connection_filter_shows_only_matching_employees(self):
        make_device(self.emp, last_seen=timezone.now())
        response = self.client.get(reverse("deployment"), {"connection": "offline"}, secure=True)
        self.assertEqual(response.context["rows"], [])
        response = self.client.get(reverse("deployment"), {"connection": "connected"}, secure=True)
        self.assertEqual([r["pk"] for r in response.context["rows"]], [self.emp.pk])

    def test_the_needs_action_counts_come_from_the_real_rows(self):
        response = self.client.get(reverse("deployment"), secure=True)
        self.assertEqual(response.context["summary"]["needs_link"], 1)
        self.assertContains(response, "Send setup link")
        make_device(self.emp, last_seen=timezone.now())
        response = self.client.get(reverse("deployment"), secure=True)
        self.assertEqual(response.context["summary"]["needs_link"], 0)
        self.assertEqual(response.context["summary"]["ready"], 1)
        self.assertContains(response, "Start monitoring")

    def test_starting_monitoring_from_the_deployment_page_returns_there(self):
        make_device(self.emp, last_seen=timezone.now())
        response = self.client.post(
            reverse("admin_monitoring_start", args=[self.emp.pk]), {"next": "deployment"}, secure=True
        )
        self.assertRedirects(response, reverse("deployment"), fetch_redirect_response=False)

    def test_sending_a_setup_link_from_the_deployment_page_returns_there(self):
        response = self.client.post(
            reverse("monitoring_setup_start", args=[self.emp.pk]), {"next": "deployment"}, secure=True
        )
        self.assertRedirects(response, reverse("deployment"), fetch_redirect_response=False)
