import datetime

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from audit.models import AuditLog
from devices.models import Device, RegistrationCode
from devices.services import hash_secret, issue_registration_code
from monitoring import services as monitoring_services

REGISTER_URL = "/api/agent/v1/devices/register/"
CONFIG_URL = "/api/agent/v1/config/"
HEARTBEAT_URL = "/api/agent/v1/heartbeat/"


def register_body(code, **overrides):
    body = {
        "registration_code": code,
        "device_name": "LAPTOP-1",
        "os_version": "Windows 11 10.0.26100",
        "architecture": "x64",
        "agent_version": "0.1.0",
    }
    body.update(overrides)
    return body


def heartbeat_body(**overrides):
    body = {
        "agent_version": "0.1.0",
        "sent_at": "2026-11-02T10:00:00Z",
        "session_type": "console",
        "queue_depth": 3,
        "monitoring_state": "active",
    }
    body.update(overrides)
    return body


class AgentApiTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b = make_org("Org A"), make_org("Org B")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.emp_a = make_user("e@a.test", Role.EMPLOYEE, cls.org_a)
        cls.emp_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b)

    def setUp(self):
        cache.clear()  # the register throttle keeps its counters in the cache
        self.api = APIClient()

    def tearDown(self):
        cache.clear()

    def new_code(self, user=None):
        user = user or self.emp_a
        code, _ = issue_registration_code(self.admin_a, user)
        return code

    def register(self, user=None, **overrides):
        response = self.api.post(REGISTER_URL, register_body(self.new_code(user), **overrides), format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def authenticate(self, credential):
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {credential}")


class RegistrationTests(AgentApiTestCase):
    def test_valid_code_registers_a_device(self):
        data = self.register()
        self.assertEqual(set(data), {"device_id", "credential", "expires_at"})
        self.assertTrue(data["expires_at"].endswith("Z"))
        device = Device.objects.get(pk=data["device_id"])
        self.assertEqual(device.user, self.emp_a)
        self.assertEqual(device.organization, self.org_a)
        self.assertEqual(device.name, "LAPTOP-1")
        self.assertTrue(AuditLog.objects.filter(action="device.registered", organization=self.org_a).exists())

    def test_only_a_hash_of_the_credential_is_stored(self):
        data = self.register()
        device = Device.objects.get(pk=data["device_id"])
        self.assertEqual(device.credential_hash, hash_secret(data["credential"]))
        self.assertNotIn(data["credential"], [device.credential_hash, device.name])

    def test_only_a_hash_of_the_registration_code_is_stored(self):
        code = self.new_code()
        record = RegistrationCode.objects.get()
        self.assertEqual(record.code_hash, hash_secret(code))
        self.assertNotEqual(record.code_hash, code)

    def test_code_works_once(self):
        code = self.new_code()
        first = self.api.post(REGISTER_URL, register_body(code), format="json")
        second = self.api.post(REGISTER_URL, register_body(code), format="json")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 403)
        self.assertEqual(second.json()["code"], "invalid_registration_code")
        self.assertEqual(Device.objects.count(), 1)

    def test_code_is_accepted_in_lower_case(self):
        response = self.api.post(REGISTER_URL, register_body(self.new_code().lower()), format="json")
        self.assertEqual(response.status_code, 201)

    def test_one_employee_can_register_several_devices(self):
        self.register(device_name="LAPTOP-1")
        self.register(device_name="LAPTOP-2")
        self.assertEqual(Device.objects.filter(user=self.emp_a).count(), 2)

    def test_unknown_expired_deactivated_all_give_the_same_answer(self):
        expired = self.new_code()
        RegistrationCode.objects.update(expires_at=timezone.now() - datetime.timedelta(minutes=1))
        fresh_for_inactive_user = self.new_code(self.emp_b)
        self.emp_b.is_active = False
        self.emp_b.save()
        answers = set()
        for code in ("NOPE-NOPE-NOPE", expired, fresh_for_inactive_user):
            response = self.api.post(REGISTER_URL, register_body(code), format="json")
            self.assertEqual(response.status_code, 403)
            answers.add((response.json()["code"], response.json()["detail"]))
        self.assertEqual(len(answers), 1)
        self.assertFalse(Device.objects.exists())

    def test_code_of_deactivated_organization_is_refused(self):
        code = self.new_code()
        self.org_a.is_active = False
        self.org_a.save()
        self.assertEqual(self.api.post(REGISTER_URL, register_body(code), format="json").status_code, 403)

    def test_organization_or_employee_ids_in_the_body_are_not_accepted(self):
        code = self.new_code()
        for field in ("organization_id", "employee_id"):
            with self.subTest(field=field):
                response = self.api.post(REGISTER_URL, register_body(code, **{field: self.org_b.pk}), format="json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "invalid_request")
        self.assertFalse(Device.objects.exists())

    def test_the_device_belongs_to_the_code_owner_not_to_anything_in_the_request(self):
        data = self.register(self.emp_b)
        device = Device.objects.get(pk=data["device_id"])
        self.assertEqual((device.user, device.organization), (self.emp_b, self.org_b))

    def test_missing_and_malformed_fields_are_rejected(self):
        code = self.new_code()
        bad_bodies = [
            {},
            register_body(code, device_name=""),
            register_body("short"),
            register_body(code, architecture="arm64"),
            register_body(code, agent_version="x" * 33),
        ]
        for body in bad_bodies:
            with self.subTest(body=body):
                self.assertEqual(self.api.post(REGISTER_URL, body, format="json").status_code, 400)

    def test_registration_attempts_are_limited(self):
        for _ in range(10):
            self.assertEqual(self.api.post(REGISTER_URL, register_body("WRONG-CODE-0000"), format="json").status_code, 403)
        response = self.api.post(REGISTER_URL, register_body("WRONG-CODE-0000"), format="json")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["code"], "rate_limited")

    def test_get_is_not_allowed(self):
        self.assertEqual(self.api.get(REGISTER_URL).status_code, 405)

    def test_the_credential_response_cannot_be_cached(self):
        response = self.api.post(REGISTER_URL, register_body(self.new_code()), format="json")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_a_new_code_replaces_the_earlier_unused_codes_of_that_employee(self):
        old = self.new_code()
        new = self.new_code()
        other_employee_code = self.new_code(self.emp_b)
        self.assertEqual(self.api.post(REGISTER_URL, register_body(old), format="json").status_code, 403)
        self.assertEqual(self.api.post(REGISTER_URL, register_body(new), format="json").status_code, 201)
        self.assertEqual(self.api.post(REGISTER_URL, register_body(other_employee_code), format="json").status_code, 201)

    def test_error_messages_do_not_repeat_the_value_that_was_sent(self):
        response = self.api.post(REGISTER_URL, register_body(self.new_code(), architecture="SECRET-ARCH"), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("SECRET-ARCH", response.content.decode())


class CredentialTests(AgentApiTestCase):
    def test_missing_header_is_401(self):
        response = self.api.get(CONFIG_URL)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "missing_credential")

    def test_unknown_credential_is_401(self):
        self.authenticate("not-a-real-credential")
        response = self.api.get(CONFIG_URL)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "invalid_credential")

    def test_expired_credential_is_401(self):
        data = self.register()
        Device.objects.update(credential_expires_at=timezone.now() - datetime.timedelta(seconds=1))
        self.authenticate(data["credential"])
        response = self.api.get(CONFIG_URL)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "credential_expired")

    def test_revoked_device_is_401(self):
        data = self.register()
        Device.objects.update(is_active=False)
        self.authenticate(data["credential"])
        response = self.api.get(CONFIG_URL)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "device_revoked")

    def test_deactivated_employee_is_403(self):
        data = self.register()
        self.emp_a.is_active = False
        self.emp_a.save()
        self.authenticate(data["credential"])
        response = self.api.get(CONFIG_URL)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "account_disabled")

    def test_deactivated_organization_is_403(self):
        data = self.register()
        self.org_a.is_active = False
        self.org_a.save()
        self.authenticate(data["credential"])
        self.assertEqual(self.api.get(CONFIG_URL).status_code, 403)

    def test_a_web_login_session_does_not_work_on_the_agent_api(self):
        self.client.force_login(self.admin_a)
        self.assertEqual(self.client.get(CONFIG_URL).status_code, 401)

    def test_the_bearer_word_is_not_case_sensitive(self):
        data = self.register()
        self.api.credentials(HTTP_AUTHORIZATION=f"bearer {data['credential']}")
        self.assertEqual(self.api.get(CONFIG_URL).status_code, 200)

    def test_the_credential_is_never_echoed_in_error_responses(self):
        self.authenticate("secret-looking-value-123")
        response = self.api.get(CONFIG_URL)
        self.assertNotIn("secret-looking-value-123", response.content.decode())


class ConfigTests(AgentApiTestCase):
    def test_config_has_the_shape_in_the_contract(self):
        self.authenticate(self.register()["credential"])
        response = self.api.get(CONFIG_URL)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), {"policy_version", "monitoring", "activity", "screenshots", "exclusions"})
        self.assertEqual(set(data["monitoring"]), {"enabled", "days", "start", "end", "timezone"})
        self.assertTrue(all(isinstance(day, int) and 0 <= day <= 6 for day in data["monitoring"]["days"]))
        self.assertRegex(data["monitoring"]["start"], r"^([01]\d|2[0-3]):[0-5]\d$")
        self.assertRegex(data["monitoring"]["end"], r"^([01]\d|2[0-3]):[0-5]\d$")
        self.assertIs(data["screenshots"]["enabled"], False)

    def test_the_monitoring_hours_come_with_their_time_zone(self):
        # The agent checks the hours in this zone, the same way the server checks them before accepting a screenshot.
        monitoring_services.update_policy(self.admin_a, self.org_a, {"timezone": "Asia/Kolkata"})
        self.authenticate(self.register()["credential"])
        self.assertEqual(self.api.get(CONFIG_URL).json()["monitoring"]["timezone"], "Asia/Kolkata")

    def test_each_device_gets_the_policy_of_its_own_organization(self):
        monitoring_services.update_policy(
            self.admin_a,
            self.org_a,
            {
                "enabled": True,
                "days": [0],
                "start_time": datetime.time(6, 0),
                "end_time": datetime.time(7, 0),
                "sample_interval_seconds": 10,
                "idle_threshold_seconds": 120,
            },
        )
        self.authenticate(self.register(self.emp_a)["credential"])
        policy_a = self.api.get(CONFIG_URL).json()
        self.authenticate(self.register(self.emp_b)["credential"])
        policy_b = self.api.get(CONFIG_URL).json()
        self.assertEqual(policy_a["monitoring"]["days"], [0])
        self.assertEqual(policy_a["activity"]["sample_interval_seconds"], 10)
        self.assertEqual(policy_b["monitoring"]["days"], [0, 1, 2, 3, 4])
        self.assertEqual(policy_b["activity"]["sample_interval_seconds"], 30)

    def test_config_and_heartbeat_calls_are_limited_per_device(self):
        self.authenticate(self.register()["credential"])
        for _ in range(30):
            self.assertEqual(self.api.get(CONFIG_URL).status_code, 200)
        limited = self.api.get(CONFIG_URL)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()["code"], "rate_limited")
        self.assertEqual(self.api.post(HEARTBEAT_URL, heartbeat_body(), format="json").status_code, 429)
        self.authenticate(self.register(self.emp_b)["credential"])
        self.assertEqual(self.api.get(CONFIG_URL).status_code, 200)

    def test_config_records_which_policy_version_the_device_has(self):
        data = self.register()
        self.authenticate(data["credential"])
        self.api.get(CONFIG_URL)
        self.assertEqual(Device.objects.get(pk=data["device_id"]).policy_version_served, "1")


class HeartbeatTests(AgentApiTestCase):
    def setUp(self):
        super().setUp()
        self.registered = self.register()
        self.authenticate(self.registered["credential"])

    def device(self):
        return Device.objects.get(pk=self.registered["device_id"])

    def test_heartbeat_stores_the_reported_status(self):
        response = self.api.post(
            HEARTBEAT_URL,
            heartbeat_body(last_successful_sync_at="2026-11-02T09:59:00Z", last_error_code="ConnectionError"),
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIs(response.json()["acknowledged"], True)
        device = self.device()
        self.assertIsNotNone(device.last_seen_at)
        self.assertTrue(device.is_online)
        self.assertEqual(device.session_type, "console")
        self.assertEqual(device.queue_depth, 3)
        self.assertEqual(device.monitoring_state, "active")
        self.assertEqual(device.last_error_code, "ConnectionError")
        self.assertEqual(device.last_successful_sync_at.isoformat(), "2026-11-02T09:59:00+00:00")

    def test_optional_fields_left_out_keep_the_stored_values_and_a_missing_error_clears(self):
        self.api.post(
            HEARTBEAT_URL,
            heartbeat_body(last_successful_sync_at="2026-11-02T09:59:00Z", last_error_code="ConnectionError"),
            format="json",
        )
        body = heartbeat_body()
        del body["monitoring_state"]
        self.api.post(HEARTBEAT_URL, body, format="json")
        device = self.device()
        self.assertEqual(device.last_successful_sync_at.isoformat(), "2026-11-02T09:59:00+00:00")
        self.assertEqual(device.monitoring_state, "active")
        self.assertEqual(device.last_error_code, "")

    def test_error_code_must_be_a_short_code_not_free_text(self):
        for value in ("typed words with spaces", "a" * 65, "semi;colon"):
            with self.subTest(value=value):
                response = self.api.post(HEARTBEAT_URL, heartbeat_body(last_error_code=value), format="json")
                self.assertEqual(response.status_code, 400)

    def test_choice_errors_do_not_repeat_the_value_that_was_sent(self):
        response = self.api.post(HEARTBEAT_URL, heartbeat_body(session_type="LEAKY-VALUE"), format="json")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("LEAKY-VALUE", response.content.decode())

    def test_heartbeat_updates_only_the_calling_device(self):
        other = self.register(self.emp_b)
        self.api.post(HEARTBEAT_URL, heartbeat_body(), format="json")
        self.assertIsNone(Device.objects.get(pk=other["device_id"]).last_seen_at)

    def test_policy_changed_flag_follows_the_policy_version(self):
        self.api.get(CONFIG_URL)
        first = self.api.post(HEARTBEAT_URL, heartbeat_body(), format="json").json()
        self.assertIs(first["policy_changed"], False)
        policy = monitoring_services.get_policy(self.org_a)
        values = {name: getattr(policy, name) for name in monitoring_services.POLICY_FIELDS}
        values["sample_interval_seconds"] = 45
        monitoring_services.update_policy(self.admin_a, self.org_a, values)
        second = self.api.post(HEARTBEAT_URL, heartbeat_body(), format="json").json()
        self.assertIs(second["policy_changed"], True)
        self.api.get(CONFIG_URL)
        third = self.api.post(HEARTBEAT_URL, heartbeat_body(), format="json").json()
        self.assertIs(third["policy_changed"], False)

    def test_invalid_heartbeats_are_rejected(self):
        bad_bodies = [
            heartbeat_body(session_type="mainframe"),
            heartbeat_body(queue_depth=-1),
            heartbeat_body(monitoring_state="sleeping"),
            heartbeat_body(organization_id=1),
            heartbeat_body(sent_at="yesterday"),
        ]
        for body in bad_bodies:
            with self.subTest(body=body):
                self.assertEqual(self.api.post(HEARTBEAT_URL, body, format="json").status_code, 400)
        missing = heartbeat_body()
        del missing["queue_depth"]
        self.assertEqual(self.api.post(HEARTBEAT_URL, missing, format="json").status_code, 400)

    def test_heartbeat_needs_a_credential(self):
        self.api.credentials()
        self.assertEqual(self.api.post(HEARTBEAT_URL, heartbeat_body(), format="json").status_code, 401)

    def test_device_is_offline_without_a_recent_heartbeat(self):
        self.assertFalse(self.device().is_online)
        Device.objects.update(last_seen_at=timezone.now() - datetime.timedelta(hours=1))
        self.assertFalse(self.device().is_online)
