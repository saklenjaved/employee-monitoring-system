"""An in-memory stand-in for screenshots.storage.S3Storage, used by every test."""


class FakeStorage:
    objects = {}  # key -> size in bytes, for images "uploaded" by a test
    deleted = []  # keys that were deleted
    uploads = []  # (key, byte_size, sha256, expires) for every upload link handed out

    @classmethod
    def reset(cls):
        cls.objects = {}
        cls.deleted = []
        cls.uploads = []

    def presign_upload(self, key, byte_size, sha256_hex, expires_seconds):
        FakeStorage.uploads.append((key, byte_size, sha256_hex, expires_seconds))
        return f"https://storage.test/put/{key}?expires={expires_seconds}", {
            "Content-Type": "image/png",
            "x-amz-checksum-sha256": "fake-checksum",
        }

    def presign_download(self, key, expires_seconds, filename=None):
        url = f"https://storage.test/get/{key}?expires={expires_seconds}"
        return url + (f"&download={filename}" if filename else "")

    def object_size(self, key):
        return FakeStorage.objects.get(key)

    def delete(self, key):
        FakeStorage.objects.pop(key, None)
        FakeStorage.deleted.append(key)

    def ensure_bucket(self):
        pass
