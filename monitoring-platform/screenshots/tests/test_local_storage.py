"""LocalStorage (development only): signed upload and download links, stored on this computer's disk."""
import hashlib
import tempfile
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings

from screenshots.storage import LocalStorage

IMAGE = b"\x89PNG fake image bytes"
SHA = hashlib.sha256(IMAGE).hexdigest()
KEY = "org-1/2026/11/16/abc.png"
LOCAL = "screenshots.storage.LocalStorage"


class LocalStorageTests(TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        settings_override = override_settings(
            SCREENSHOT_STORAGE=LOCAL,
            SCREENSHOT_LOCAL_DIR=folder.name,
            DEV_MODE=True,
            AGENT_BASE_URL="https://127.0.0.1:8443/api/agent/v1",
        )
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        self.folder = Path(folder.name)
        self.storage = LocalStorage()

    def upload_path(self, **kwargs):
        url, headers = self.storage.presign_upload(KEY, kwargs.get("size", len(IMAGE)), kwargs.get("sha", SHA), kwargs.get("seconds", 300))
        return urlparse(url).path

    def put(self, path, body=IMAGE):
        return self.client.put(path, data=body, content_type="image/png")

    def test_the_upload_link_is_https_on_the_agent_address_and_has_the_headers_to_send(self):
        url, headers = self.storage.presign_upload(KEY, len(IMAGE), SHA, 300)
        self.assertTrue(url.startswith("https://127.0.0.1:8443/storage/upload/"))
        self.assertEqual(headers, {"Content-Type": "image/png"})

    def test_a_valid_upload_is_stored_and_its_size_is_known(self):
        response = self.put(self.upload_path())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.storage.object_size(KEY), len(IMAGE))
        self.assertEqual((self.folder / KEY).read_bytes(), IMAGE)

    def test_a_different_file_than_authorized_is_refused_and_not_stored(self):
        for body in (b"other bytes of another size", IMAGE[:-1] + b"X"):
            with self.subTest(body=body):
                self.assertEqual(self.put(self.upload_path(), body).status_code, 400)
        self.assertIsNone(self.storage.object_size(KEY))

    def test_a_forged_or_damaged_link_is_refused(self):
        path = self.upload_path()
        self.assertEqual(self.put(path[:-6] + "xxxxx/").status_code, 403)
        self.assertIsNone(self.storage.object_size(KEY))

    def test_an_expired_link_is_refused(self):
        path = self.upload_path(seconds=60)
        with mock.patch("screenshots.local_views.time.time", return_value=10**10):
            self.assertEqual(self.put(path).status_code, 403)
        self.assertIsNone(self.storage.object_size(KEY))

    def test_an_upload_link_cannot_be_used_to_download(self):
        self.put(self.upload_path())
        self.assertEqual(self.client.get(self.upload_path().replace("/upload/", "/file/")).status_code, 403)

    def test_uploads_only_accept_put_and_downloads_only_get(self):
        self.assertEqual(self.client.get(self.upload_path()).status_code, 405)
        link = self.storage.presign_download(KEY, 60)
        self.assertEqual(self.client.put(link, data=b"x", content_type="image/png").status_code, 405)

    def test_a_download_link_sends_the_image_without_caching(self):
        self.put(self.upload_path())
        response = self.client.get(self.storage.presign_download(KEY, 60))
        self.assertEqual(b"".join(response.streaming_content), IMAGE)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertNotIn("attachment", response.get("Content-Disposition", ""))

    def test_a_download_with_a_file_name_is_an_attachment(self):
        self.put(self.upload_path())
        response = self.client.get(self.storage.presign_download(KEY, 60, "shot.png"))
        self.assertIn('attachment; filename="shot.png"', response["Content-Disposition"])
        response.close()  # Windows cannot delete the temp folder while the file is still open

    def test_an_expired_download_link_is_refused(self):
        self.put(self.upload_path())
        link = self.storage.presign_download(KEY, 60)
        with mock.patch("screenshots.local_views.time.time", return_value=10**10):
            self.assertEqual(self.client.get(link).status_code, 403)

    def test_delete_removes_the_file_and_a_missing_file_is_fine(self):
        self.put(self.upload_path())
        self.storage.delete(KEY)
        self.assertIsNone(self.storage.object_size(KEY))
        self.storage.delete(KEY)

    def test_a_key_that_leaves_the_storage_folder_is_refused(self):
        for key in ("../outside.png", "org-1/../../outside.png", "/etc/passwd"):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    self.storage.presign_upload(key, 1, SHA, 60)

    def test_ensure_bucket_creates_the_folder(self):
        target = self.folder / "made-later"
        with override_settings(SCREENSHOT_LOCAL_DIR=str(target)):
            LocalStorage().ensure_bucket()
        self.assertTrue(target.is_dir())


class LocalStorageGuardTests(TestCase):
    def test_it_refuses_to_start_outside_development(self):
        with override_settings(DEV_MODE=False), self.assertRaises(ImproperlyConfigured):
            LocalStorage()

    def test_the_links_are_not_found_when_local_storage_is_not_in_use(self):
        self.assertEqual(self.client.put("/storage/upload/anything/", data=b"x", content_type="image/png").status_code, 404)
        self.assertEqual(self.client.get("/storage/file/anything/").status_code, 404)
