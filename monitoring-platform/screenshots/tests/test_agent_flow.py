import datetime
import hashlib
import uuid

from django.utils import timezone

from accounts.models import Role
from accounts.tests.factories import make_user
from audit.models import AuditLog
from devices.tests.test_agent_api import CONFIG_URL, HEARTBEAT_URL, AgentApiTestCase, heartbeat_body
from monitoring.services import update_policy as update_monitoring_policy
from screenshots import services
from screenshots.models import Screenshot, ScreenshotStatus
from screenshots.tests.fakes import FakeStorage
from workforce.models import Team, UserTeam

AUTHORIZE_URL = "/api/agent/v1/screenshots/authorize/"
ALWAYS_OPEN = {
    "enabled": True,
    "days": [0, 1, 2, 3, 4, 5, 6],
    "start_time": datetime.time(0, 0),
    "end_time": datetime.time(23, 59, 59, 999999),
}
SETTINGS = {"enabled": True, "interval_seconds": 600, "retention_days": 30, "blur_level": "medium"}


def complete_url(screenshot_id):
    return f"/api/agent/v1/screenshots/{screenshot_id}/complete/"


def metadata(**overrides):
    body = {
        "screenshot_id": str(uuid.uuid4()),
        "captured_at": timezone.now().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "content_type": "image/png",
        "byte_size": 2048,
        "sha256": hashlib.sha256(b"image").hexdigest(),
        "width": 1920,
        "height": 1080,
        "redaction_applied": False,
    }
    body.update(overrides)
    return body


class ScreenshotApiTestCase(AgentApiTestCase):
    def setUp(self):
        super().setUp()
        FakeStorage.reset()
        update_monitoring_policy(self.admin_a, self.org_a, ALWAYS_OPEN)
        services.update_policy(self.admin_a, self.org_a, SETTINGS)
        self.registered = self.register()
        self.authenticate(self.registered["credential"])
        self.device = self.emp_a.devices.get()

    def authorize(self, **overrides):
        return self.api.post(AUTHORIZE_URL, metadata(**overrides), format="json")


class AuthorizeTests(ScreenshotApiTestCase):
    def test_a_screenshot_is_authorized_and_the_agent_gets_a_signed_upload_link(self):
        body = metadata()
        response = self.api.post(AUTHORIZE_URL, body, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data["screenshot_id"], body["screenshot_id"])
        self.assertEqual(data["upload_method"], "PUT")
        self.assertTrue(data["upload_url"].startswith("https://storage.test/put/"))
        self.assertEqual(data["upload_headers"]["Content-Type"], "image/png")
        self.assertTrue(data["expires_at"].endswith("Z"))
        self.assertEqual(response.headers["Cache-Control"], "no-store")

        shot = Screenshot.objects.get()
        self.assertEqual((shot.organization, shot.user, shot.device), (self.org_a, self.emp_a, self.device))
        self.assertEqual(shot.status, ScreenshotStatus.AUTHORIZED)
        self.assertEqual((shot.byte_size, shot.sha256), (2048, body["sha256"]))
        # The link is for this object, this size and this hash only, and lasts five minutes.
        self.assertEqual(FakeStorage.uploads, [(shot.object_key, 2048, body["sha256"], 300)])

    def test_the_storage_key_is_chosen_by_the_server_and_holds_no_personal_data(self):
        body = metadata()
        self.authorize(**body)
        key = Screenshot.objects.get().object_key
        self.assertRegex(key, rf"^org-{self.org_a.pk}/\d{{4}}/\d{{2}}/\d{{2}}/[0-9a-f]{{32}}\.png$")
        self.assertNotIn(body["screenshot_id"], key)
        self.assertNotIn("e@a.test", key)

    def test_every_policy_check_is_repeated_on_the_server(self):
        cases = {}

        def turn_off(**values):
            services.update_policy(self.admin_a, self.org_a, values)

        def scenario(name, setup):
            cases[name] = setup

        scenario("switched off", lambda: turn_off(enabled=False))
        scenario("monitoring switched off", lambda: update_monitoring_policy(self.admin_a, self.org_a, {"enabled": False}))
        scenario("no monitoring days", lambda: update_monitoring_policy(self.admin_a, self.org_a, {"days": []}))
        scenario(
            "employee excluded", lambda: services.add_exclusion(self.admin_a, self.org_a, user=self.emp_a)
        )
        denied = set()
        for name, setup in cases.items():
            with self.subTest(case=name):
                setup()
                response = self.authorize()
                self.assertEqual(response.status_code, 403, name)
                self.assertEqual(response.json()["code"], "policy_denies")
                denied.add((response.json()["code"], response.json()["detail"]))
                # put everything back for the next case
                services.update_policy(self.admin_a, self.org_a, SETTINGS)
                update_monitoring_policy(self.admin_a, self.org_a, ALWAYS_OPEN)
                services.ScreenshotExclusion.objects.all().delete()
        self.assertEqual(len(denied), 1)  # the reason is never given
        self.assertFalse(Screenshot.objects.exists())

    def test_a_team_exclusion_applies_to_its_members(self):
        team = Team.objects.create(organization=self.org_a, name="Private")
        UserTeam.objects.create(organization=self.org_a, team=team, user=self.emp_a)
        services.add_exclusion(self.admin_a, self.org_a, team=team)
        self.assertEqual(self.authorize().status_code, 403)

    def test_screenshots_stay_off_until_the_admin_has_chosen_everything(self):
        from screenshots.models import ScreenshotPolicy

        ScreenshotPolicy.objects.all().delete()
        self.assertEqual(self.authorize().status_code, 403)
        ScreenshotPolicy.objects.create(organization=self.org_a, enabled=True)  # enabled, but nothing chosen
        self.assertEqual(self.authorize().status_code, 403)

    def test_outside_the_monitoring_hours_is_denied(self):
        # Monitoring runs only on a weekday that is not today.
        today = timezone.now().weekday()
        update_monitoring_policy(self.admin_a, self.org_a, {"days": [(today + 1) % 7]})
        self.assertEqual(self.authorize().status_code, 403)

    def test_another_organizations_policy_does_not_matter(self):
        services.update_policy(self.admin_a, self.org_a, {"enabled": False})
        other = self.register(self.emp_b)
        self.authenticate(other["credential"])
        self.assertEqual(self.authorize().status_code, 403)  # organization B never configured screenshots

    def test_bad_data_is_refused(self):
        for overrides in (
            {"organization_id": self.org_b.pk},
            {"content_type": "image/jpeg"},
            {"byte_size": 0},
            {"byte_size": 10 * 1024 * 1024 + 1},
            {"sha256": hashlib.sha256(b"x").hexdigest().upper()},
            {"sha256": "abc"},
            {"screenshot_id": str(uuid.uuid1())},
            {"screenshot_id": "not-a-uuid"},
            {"width": 0},
            {"captured_at": "yesterday"},
            {"captured_at": (timezone.now() + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")},
            {"captured_at": (timezone.now() - datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")},
        ):
            with self.subTest(overrides=overrides):
                response = self.authorize(**overrides)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(response.json()["code"], "invalid_request")
        self.assertFalse(Screenshot.objects.exists())

    def test_asking_again_for_an_unfinished_screenshot_gives_a_new_link_for_the_same_object(self):
        body = metadata()
        first = self.api.post(AUTHORIZE_URL, body, format="json")
        second = self.api.post(AUTHORIZE_URL, body, format="json")
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual(Screenshot.objects.count(), 1)
        self.assertEqual(len({upload[0] for upload in FakeStorage.uploads}), 1)

    def test_a_finished_screenshot_cannot_be_authorized_again(self):
        body = metadata()
        self.api.post(AUTHORIZE_URL, body, format="json")
        key = Screenshot.objects.get().object_key
        FakeStorage.objects[key] = 2048
        self.api.post(complete_url(body["screenshot_id"]), {"status": "uploaded"}, format="json")
        again = self.api.post(AUTHORIZE_URL, body, format="json")
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.json()["code"], "already_finished")

    def test_the_same_id_on_two_devices_is_two_screenshots(self):
        body = metadata()
        self.api.post(AUTHORIZE_URL, body, format="json")
        admin_b = make_user("admin@b.test", Role.ADMIN, self.org_b)
        update_monitoring_policy(admin_b, self.org_b, ALWAYS_OPEN)
        services.update_policy(admin_b, self.org_b, SETTINGS)
        self.authenticate(self.register(self.emp_b)["credential"])
        self.assertEqual(self.api.post(AUTHORIZE_URL, body, format="json").status_code, 200)
        self.assertEqual(Screenshot.objects.count(), 2)
        self.assertEqual(len({shot.object_key for shot in Screenshot.objects.all()}), 2)

    def test_a_credential_is_required(self):
        self.api.credentials()
        self.assertEqual(self.authorize().status_code, 401)
        self.assertFalse(Screenshot.objects.exists())


class CompleteTests(ScreenshotApiTestCase):
    def authorized(self):
        body = metadata()
        self.api.post(AUTHORIZE_URL, body, format="json")
        return body["screenshot_id"], Screenshot.objects.get(screenshot_id=body["screenshot_id"])

    def test_a_finished_upload_is_recorded_and_audited(self):
        screenshot_id, shot = self.authorized()
        FakeStorage.objects[shot.object_key] = 2048
        response = self.api.post(complete_url(screenshot_id), {"status": "uploaded"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"screenshot_id": screenshot_id, "status": "completed"})
        shot.refresh_from_db()
        self.assertEqual(shot.status, ScreenshotStatus.UPLOADED)
        self.assertIsNotNone(shot.completed_at)
        entry = AuditLog.objects.get(action="screenshot.uploaded")
        self.assertEqual((entry.actor, entry.organization, entry.target_id), (self.emp_a, self.org_a, str(shot.pk)))

    def test_the_agent_cannot_claim_an_upload_that_is_not_in_storage(self):
        screenshot_id, shot = self.authorized()
        response = self.api.post(complete_url(screenshot_id), {"status": "uploaded"}, format="json")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "object_missing")
        FakeStorage.objects[shot.object_key] = 999  # wrong size
        self.assertEqual(self.api.post(complete_url(screenshot_id), {"status": "uploaded"}, format="json").status_code, 409)
        shot.refresh_from_db()
        self.assertEqual(shot.status, ScreenshotStatus.AUTHORIZED)

    def test_a_failed_upload_is_recorded_and_nothing_is_kept(self):
        screenshot_id, shot = self.authorized()
        FakeStorage.objects[shot.object_key] = 100  # a partial object
        response = self.api.post(
            complete_url(screenshot_id), {"status": "failed", "error_code": "upload_rejected"}, format="json"
        )
        self.assertEqual(response.json()["status"], "upload_failed")
        shot.refresh_from_db()
        self.assertEqual((shot.status, shot.error_code), (ScreenshotStatus.FAILED, "upload_rejected"))
        self.assertIn(shot.object_key, FakeStorage.deleted)
        self.assertTrue(AuditLog.objects.filter(action="screenshot.upload_failed", detail="upload_rejected").exists())

    def test_reporting_twice_changes_nothing(self):
        screenshot_id, shot = self.authorized()
        FakeStorage.objects[shot.object_key] = 2048
        self.api.post(complete_url(screenshot_id), {"status": "uploaded"}, format="json")
        again = self.api.post(complete_url(screenshot_id), {"status": "failed"}, format="json")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["status"], "completed")
        shot.refresh_from_db()
        self.assertEqual(shot.status, ScreenshotStatus.UPLOADED)
        self.assertEqual(AuditLog.objects.filter(action__startswith="screenshot.upload").count(), 1)

    def test_another_devices_screenshot_is_not_found(self):
        screenshot_id, shot = self.authorized()
        self.authenticate(self.register(self.emp_b)["credential"])
        response = self.api.post(complete_url(screenshot_id), {"status": "failed"}, format="json")
        self.assertEqual(response.status_code, 404)
        shot.refresh_from_db()
        self.assertEqual(shot.status, ScreenshotStatus.AUTHORIZED)
        self.assertEqual(self.api.post(complete_url(uuid.uuid4()), {"status": "failed"}, format="json").status_code, 404)

    def test_bad_reports_are_refused(self):
        screenshot_id, _ = self.authorized()
        for body in (
            {},
            {"status": "done"},
            {"status": "failed", "error_code": "free text with spaces"},
            {"status": "failed", "organization_id": 1},
        ):
            with self.subTest(body=body):
                self.assertEqual(self.api.post(complete_url(screenshot_id), body, format="json").status_code, 400)


class PolicyDeliveryTests(ScreenshotApiTestCase):
    def config(self):
        return self.api.get(CONFIG_URL).json()

    def test_the_agent_gets_the_screenshot_settings(self):
        shots = self.config()["screenshots"]
        self.assertEqual(shots, {"enabled": True, "interval_seconds": 600, "blur_level": "medium"})

    def test_screenshots_are_off_in_the_policy_until_configured(self):
        services.update_policy(self.admin_a, self.org_a, {"enabled": False})
        self.assertEqual(self.config()["screenshots"], {"enabled": False})

    def test_an_excluded_employee_gets_a_policy_with_screenshots_off_and_others_do_not(self):
        services.add_exclusion(self.admin_a, self.org_a, user=self.emp_a)
        self.assertEqual(self.config()["screenshots"], {"enabled": False})
        other_employee_policy = services.agent_screenshot_policy(self.device)
        self.assertEqual(other_employee_policy, {"enabled": False})

    def test_the_policy_version_goes_up_when_screenshot_settings_or_exclusions_change(self):
        before = int(self.config()["policy_version"])
        services.update_policy(self.admin_a, self.org_a, {"interval_seconds": 900})
        after_settings = int(self.config()["policy_version"])
        colleague = make_user("colleague@a.test", Role.EMPLOYEE, self.org_a)
        services.add_exclusion(self.admin_a, self.org_a, user=colleague)
        after_exclusion = int(self.config()["policy_version"])
        self.assertTrue(before < after_settings < after_exclusion)

    def test_heartbeat_tells_the_agent_when_the_screenshot_policy_changed(self):
        self.config()
        self.assertIs(self.api.post(HEARTBEAT_URL, heartbeat_body(), format="json").json()["policy_changed"], False)
        services.update_policy(self.admin_a, self.org_a, {"interval_seconds": 900})
        self.assertIs(self.api.post(HEARTBEAT_URL, heartbeat_body(), format="json").json()["policy_changed"], True)

    def test_the_interval_sent_to_the_agent_is_within_the_contract(self):
        interval = self.config()["screenshots"]["interval_seconds"]
        self.assertTrue(60 <= interval <= 3600)
