import base64
import hashlib
from datetime import datetime, timedelta, timezone
from io import StringIO
from unittest import mock
from urllib.parse import parse_qs, urlparse

from botocore.exceptions import ClientError
from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone as dj_timezone

from accounts.models import Role
from accounts.tests.factories import make_org, make_user
from analytics.tests.helpers import make_device
from audit.models import AuditLog
from screenshots import services
from screenshots.models import Screenshot, ScreenshotExclusion, ScreenshotPolicy, ScreenshotStatus
from screenshots.storage import S3Storage
from screenshots.tests.fakes import FakeStorage
from screenshots.tests.test_pages import make_shot

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


class CleanupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a, cls.org_b, cls.org_c = make_org("A"), make_org("B"), make_org("C")
        cls.admin_a = make_user("admin@a.test", Role.ADMIN, cls.org_a)
        cls.admin_b = make_user("admin@b.test", Role.ADMIN, cls.org_b)
        cls.e_a = make_user("e@a.test", Role.EMPLOYEE, cls.org_a)
        cls.e_b = make_user("e@b.test", Role.EMPLOYEE, cls.org_b)
        cls.e_c = make_user("e@c.test", Role.EMPLOYEE, cls.org_c)
        cls.d_a, cls.d_b, cls.d_c = make_device(cls.e_a), make_device(cls.e_b), make_device(cls.e_c)
        services.update_policy(cls.admin_a, cls.org_a, {"interval_seconds": 600, "retention_days": 30, "blur_level": "low"})
        services.update_policy(cls.admin_b, cls.org_b, {"interval_seconds": 600, "retention_days": 7, "blur_level": "low"})
        # Organization C never chose a retention period.

    def setUp(self):
        FakeStorage.reset()

    def test_each_organization_is_cleaned_by_its_own_retention_period(self):
        old_a = make_shot(self.e_a, self.d_a, NOW - timedelta(days=31))
        new_a = make_shot(self.e_a, self.d_a, NOW - timedelta(days=29))
        old_b = make_shot(self.e_b, self.d_b, NOW - timedelta(days=8))
        new_b = make_shot(self.e_b, self.d_b, NOW - timedelta(days=6))
        no_period = make_shot(self.e_c, self.d_c, NOW - timedelta(days=3000))
        for shot in (old_a, new_a, old_b, new_b, no_period):
            FakeStorage.objects[shot.object_key] = 1000
        self.assertEqual(services.delete_expired(NOW), 2)
        self.assertEqual(set(Screenshot.objects.values_list("pk", flat=True)), {new_a.pk, new_b.pk, no_period.pk})
        self.assertEqual(set(FakeStorage.deleted), {old_a.object_key, old_b.object_key})
        self.assertEqual(
            list(
                AuditLog.objects.filter(action="screenshot.retention_deleted")
                .order_by("organization_id")
                .values_list("organization_id", "detail")
            ),
            [(self.org_a.pk, "1 older than 30 days"), (self.org_b.pk, "1 older than 7 days")],
        )

    def test_failed_and_pending_screenshots_are_cleaned_too(self):
        failed = make_shot(self.e_a, self.d_a, NOW - timedelta(days=40), ScreenshotStatus.FAILED)
        self.assertEqual(services.delete_expired(NOW), 1)
        self.assertFalse(Screenshot.objects.filter(pk=failed.pk).exists())

    def test_nothing_happens_when_nothing_is_old(self):
        make_shot(self.e_a, self.d_a, NOW)
        self.assertEqual(services.delete_expired(NOW), 0)
        self.assertFalse(AuditLog.objects.filter(action="screenshot.retention_deleted").exists())

    def test_unfinished_uploads_are_given_up_after_an_hour(self):
        stale = make_shot(self.e_a, self.d_a, NOW, ScreenshotStatus.AUTHORIZED)
        fresh = make_shot(self.e_a, self.d_a, NOW, ScreenshotStatus.AUTHORIZED)
        done = make_shot(self.e_a, self.d_a, NOW, ScreenshotStatus.UPLOADED)
        Screenshot.objects.filter(pk=stale.pk).update(created_at=NOW - timedelta(hours=2))
        Screenshot.objects.filter(pk__in=[fresh.pk, done.pk]).update(created_at=NOW - timedelta(minutes=10))
        FakeStorage.objects[stale.object_key] = 5
        self.assertEqual(services.give_up_unfinished(NOW), 1)
        stale.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual((stale.status, stale.error_code), (ScreenshotStatus.FAILED, "upload_expired"))
        self.assertEqual(fresh.status, ScreenshotStatus.AUTHORIZED)
        self.assertEqual(FakeStorage.deleted, [stale.object_key])

    def test_a_link_expires_before_an_unfinished_upload_is_given_up(self):
        # A late upload into a row that already failed would stay in storage until retention, so the
        # upload link must run out before the give-up time.
        self.assertLess(settings.SCREENSHOT_UPLOAD_LINK_SECONDS, settings.SCREENSHOT_UPLOAD_GIVE_UP_SECONDS)

    def test_the_command_runs_both_steps(self):
        make_shot(self.e_a, self.d_a, dj_timezone.now() - timedelta(days=400))
        output = StringIO()
        call_command("cleanup_screenshots", stdout=output)
        self.assertIn("Deleted 1 expired", output.getvalue())

    def test_init_storage_command(self):
        output = StringIO()
        call_command("init_storage", stdout=output)
        self.assertIn("ready", output.getvalue())


class PolicyModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = make_org("Org")
        cls.admin = make_user("admin@o.test", Role.ADMIN, cls.org)

    def test_there_are_no_defaults_for_the_choices_and_screenshots_start_off(self):
        policy = services.get_policy(self.org)
        self.assertFalse(policy.enabled)
        self.assertFalse(policy.is_configured)
        self.assertFalse(policy.is_active)
        self.assertEqual((policy.interval_seconds, policy.retention_days, policy.blur_level), (None, None, ""))
        self.assertFalse(policy.managers_can_download)
        self.assertTrue(policy.managers_can_view)

    def test_the_service_refuses_to_switch_on_an_unconfigured_policy(self):
        with self.assertRaises(ValueError):
            services.update_policy(self.admin, self.org, {"enabled": True})
        self.assertFalse(services.get_policy(self.org).enabled)

    def test_active_needs_both_the_switch_and_the_choices(self):
        services.update_policy(self.admin, self.org, {"interval_seconds": 600, "retention_days": 30, "blur_level": "none"})
        self.assertTrue(services.get_policy(self.org).is_configured)
        self.assertFalse(services.get_policy(self.org).is_active)
        services.update_policy(self.admin, self.org, {"enabled": True})
        self.assertTrue(services.get_policy(self.org).is_active)

    def test_an_exclusion_must_be_a_team_or_an_employee_and_never_both(self):
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            ScreenshotExclusion.objects.create(organization=self.org)
        with self.assertRaises(ValueError):
            services.add_exclusion(self.admin, self.org)


class S3LinkTests(SimpleTestCase):
    """The real storage class, without any network: signed links are made offline."""

    def storage(self):
        from django.test import override_settings

        with override_settings(
            S3_ENDPOINT_URL="https://s3.test:9100", S3_ACCESS_KEY_ID="key", S3_SECRET_ACCESS_KEY="secret", S3_CA_BUNDLE=""
        ):
            return S3Storage()

    def test_the_upload_link_is_locked_to_one_object_size_and_hash(self):
        digest = hashlib.sha256(b"image").hexdigest()
        url, headers = self.storage().presign_upload("org-1/2026/11/02/abc.png", 2048, digest, 300)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.path, "/employee-monitoring-screenshots/org-1/2026/11/02/abc.png")
        self.assertEqual(query["X-Amz-Expires"], ["300"])
        self.assertEqual(query["X-Amz-SignedHeaders"], ["content-length;content-type;host;x-amz-checksum-sha256"])
        self.assertIn("X-Amz-Signature", query)
        expected = base64.b64encode(bytes.fromhex(digest)).decode()
        self.assertEqual(headers, {"Content-Type": "image/png", "x-amz-checksum-sha256": expected})

    def test_the_download_link_is_short_and_can_force_a_file_name(self):
        storage = self.storage()
        plain = parse_qs(urlparse(storage.presign_download("k.png", 60)).query)
        self.assertEqual(plain["X-Amz-Expires"], ["60"])
        self.assertNotIn("response-content-disposition", plain)
        self.assertEqual(plain["response-cache-control"], ["no-store, private"])
        named = parse_qs(urlparse(storage.presign_download("k.png", 60, "shot.png")).query)
        self.assertEqual(named["response-content-disposition"], ['attachment; filename="shot.png"'])

    def test_a_failed_public_access_block_is_an_error_on_real_s3_only(self):
        denied = ClientError({"Error": {"Code": "AccessDenied"}}, "PutPublicAccessBlock")
        with override_settings(S3_ENDPOINT_URL="", S3_ACCESS_KEY_ID="", S3_CA_BUNDLE=""):
            storage = S3Storage()
            with mock.patch.object(storage, "_client") as client:
                client.put_public_access_block.side_effect = denied
                with self.assertRaises(ClientError):
                    storage.ensure_bucket()
        with override_settings(S3_ENDPOINT_URL="https://s3.test:9100", S3_ACCESS_KEY_ID="", S3_CA_BUNDLE=""):
            storage = S3Storage()
            with mock.patch.object(storage, "_client") as client:
                client.put_public_access_block.side_effect = denied
                storage.ensure_bucket()  # the local stand-in may not support this call
