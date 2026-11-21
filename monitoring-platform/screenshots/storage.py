"""Where screenshot images live: a private S3 bucket (TAD section 7).

The Django server never handles image bytes. It only hands out short-lived, signed links:
- the agent uploads straight to the bucket with a link that works for one object,
- a person looks at or downloads an image through a link that works for a minute.

The code below is the only place that talks to S3. Tests use a fake with the same methods
(screenshots/tests/fakes.py), selected with the SCREENSHOT_STORAGE setting.
"""
import base64
import time
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings
from django.core import signing
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

_instances = {}

# Separate salts, so an upload link can never be used as a download link.
UPLOAD_SALT = "screenshots.local.upload"
DOWNLOAD_SALT = "screenshots.local.download"


def get_storage():
    """The storage class named in settings, created once per class."""
    path = settings.SCREENSHOT_STORAGE
    if path not in _instances:
        _instances[path] = import_string(path)()
    return _instances[path]


class LocalStorage:
    """Images on this computer's disk, for development only. Production uses S3Storage.

    This is a deliberate exception to "Django never handles image bytes": with no bucket, the server
    itself receives the upload and sends the file. The links keep the same shape as S3's, signed,
    expiring and locked to one object, one size and one SHA-256, and screenshots/local_views.py checks them.
    """

    def __init__(self):
        if not settings.DEV_MODE:
            raise ImproperlyConfigured("LocalStorage is for development only. Use S3Storage in production.")

    def _folder(self):
        return Path(settings.SCREENSHOT_LOCAL_DIR)

    def path_for(self, key):
        """The file for an object key. A key that would leave the storage folder is refused."""
        folder = self._folder().resolve()
        path = (folder / key).resolve()
        if folder not in path.parents:
            raise ValueError("object key is outside the storage folder")
        return path

    def _upload_base(self):
        # The agent uploads over https to the same server that serves its API.
        return settings.AGENT_BASE_URL.split("/api/agent/v1")[0].rstrip("/")

    def presign_upload(self, key, byte_size, sha256_hex, expires_seconds):
        self.path_for(key)  # refuse a bad key before a link exists
        token = signing.dumps(
            {"key": key, "size": byte_size, "sha": sha256_hex, "exp": time.time() + expires_seconds}, salt=UPLOAD_SALT
        )
        return f"{self._upload_base()}/storage/upload/{token}/", {"Content-Type": "image/png"}

    def presign_download(self, key, expires_seconds, filename=None):
        self.path_for(key)
        token = signing.dumps({"key": key, "name": filename or "", "exp": time.time() + expires_seconds}, salt=DOWNLOAD_SALT)
        return f"/storage/file/{token}/"  # relative, so the browser stays on the site the person is using

    def object_size(self, key):
        path = self.path_for(key)
        return path.stat().st_size if path.is_file() else None

    def delete(self, key):
        self.path_for(key).unlink(missing_ok=True)

    def ensure_bucket(self):
        self._folder().mkdir(parents=True, exist_ok=True)


class S3Storage:
    def __init__(self):
        options = {
            "region_name": settings.S3_REGION,
            "config": Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            "verify": settings.S3_CA_BUNDLE or True,  # certificate checking is never switched off
        }
        if settings.S3_ENDPOINT_URL:  # empty means the real Amazon S3
            options["endpoint_url"] = settings.S3_ENDPOINT_URL
        if settings.S3_ACCESS_KEY_ID:  # empty means the server's own role or credentials
            options["aws_access_key_id"] = settings.S3_ACCESS_KEY_ID
            options["aws_secret_access_key"] = settings.S3_SECRET_ACCESS_KEY
        self._client = boto3.client("s3", **options)
        self._bucket = settings.S3_BUCKET

    def presign_upload(self, key, byte_size, sha256_hex, expires_seconds):
        """A signed PUT link for one object, and the headers the uploader must send with it.

        The link only works for this key, this exact size and this exact SHA-256, so a different
        file cannot be uploaded with it.
        """
        checksum = base64.b64encode(bytes.fromhex(sha256_hex)).decode("ascii")
        url = self._client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ContentType": "image/png",
                "ContentLength": byte_size,
                "ChecksumSHA256": checksum,
            },
            ExpiresIn=expires_seconds,
            HttpMethod="PUT",
        )
        return url, {"Content-Type": "image/png", "x-amz-checksum-sha256": checksum}

    def presign_download(self, key, expires_seconds, filename=None):
        """A signed GET link. With a filename the browser saves the file instead of showing it."""
        params = {
            "Bucket": self._bucket,
            "Key": key,
            "ResponseCacheControl": "no-store, private",  # the browser must not keep the image after the link ends
        }
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        return self._client.generate_presigned_url("get_object", Params=params, ExpiresIn=expires_seconds)

    def object_size(self, key):
        """The size of the stored object in bytes, or None if there is none."""
        try:
            return self._client.head_object(Bucket=self._bucket, Key=key)["ContentLength"]
        except ClientError:
            return None

    def delete(self, key):
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def ensure_bucket(self):
        """Create the bucket if it does not exist and block all public access. For development and first setup."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            self._client.create_bucket(Bucket=self._bucket)
        try:
            self._client.put_public_access_block(
                Bucket=self._bucket,
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                },
            )
        except ClientError:
            if not settings.S3_ENDPOINT_URL:  # real Amazon S3 must block public access; only the local stand-in may skip it
                raise
