"""Direct upload to private S3 through a pre-signed URL (TAD 9). Behind an interface so tests mock it.

The image bytes never pass through the Django application server. The URL is used once,
never cached and never logged.
"""

from __future__ import annotations

from typing import Mapping, Protocol

from winagent.http_client import HttpClient


class Uploader(Protocol):
    def put(self, upload_url: str, data: bytes, headers: Mapping[str, str]) -> None: ...


class PresignedS3Uploader:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def put(self, upload_url: str, data: bytes, headers: Mapping[str, str]) -> None:
        self._http.put_absolute(upload_url, data, headers)
