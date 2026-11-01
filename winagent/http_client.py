"""HTTPS client for agent-to-backend calls (TAD 11, security rule).

- Only https:// URLs. TLS certificate verification stays on (verify=True is explicit).
- The device token is sent only in the Authorization header and never logged.
- Errors are split into three kinds so callers can choose the right behavior:
  AuthError (stop sync, re-register), TransientError (back off and retry),
  PermanentError (drop the request and report it).
"""

from __future__ import annotations

from typing import Any, Callable, Mapping
from urllib.parse import urljoin, urlparse

import requests


class HttpError(Exception):
    def __init__(self, status: int | None, code: str) -> None:
        super().__init__(f"HTTP error: status={status} code={code}")
        self.status = status
        self.code = code


class AuthError(HttpError):
    """401 or 403. Stop sync and request re-registration. Do not retry forever."""


class TransientError(HttpError):
    """Network failure, 429 or 5xx. Safe to retry with backoff."""


class PermanentError(HttpError):
    """Other 4xx. Retrying the same request will not help."""


class HttpClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        token_provider: Callable[[], str | None],
        session: Any | None = None,
        ca_bundle: str | None = None,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https":
            raise ValueError("base_url must use https")
        self._base = base_url.rstrip("/") + "/"
        self._timeout = timeout_seconds
        self._token_provider = token_provider
        self._session = session or requests.Session()
        # Verification is always on. A CA bundle only changes which authority is trusted (demo certificate).
        self._verify: str | bool = ca_bundle or True

    def post_json(self, path: str, body: Mapping[str, Any], auth: bool = True) -> dict[str, Any]:
        return self._request("POST", path, body, auth)

    def get_json(self, path: str, auth: bool = True) -> dict[str, Any]:
        return self._request("GET", path, None, auth)

    def put_absolute(self, url: str, data: bytes, headers: Mapping[str, str]) -> None:
        """Direct upload to a pre-signed S3 URL. The URL is never logged or stored."""
        if urlparse(url).scheme != "https":
            raise PermanentError(None, "upload_url_not_https")
        try:
            resp = self._session.request("PUT", url, data=data, headers=dict(headers), timeout=self._timeout, verify=self._verify)
        except requests.RequestException as exc:
            raise TransientError(None, type(exc).__name__) from None
        if resp.status_code >= 500 or resp.status_code == 429:
            raise TransientError(resp.status_code, "upload_failed")
        if resp.status_code >= 400:
            raise PermanentError(resp.status_code, "upload_rejected")

    def _request(self, method: str, path: str, body: Mapping[str, Any] | None, auth: bool) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if auth:
            token = self._token_provider()
            if not token:
                raise AuthError(None, "not_registered")
            headers["Authorization"] = f"Bearer {token}"
        url = urljoin(self._base, path.lstrip("/"))
        try:
            resp = self._session.request(method, url, json=body, headers=headers, timeout=self._timeout, verify=self._verify)
        except requests.RequestException as exc:
            # Log only the exception class. Messages can contain URLs.
            raise TransientError(None, type(exc).__name__) from None
        return self._parse(resp)

    @staticmethod
    def _parse(resp: Any) -> dict[str, Any]:
        status = resp.status_code
        if 200 <= status < 300:
            try:
                data = resp.json()
            except ValueError:
                raise PermanentError(status, "invalid_json") from None
            return data if isinstance(data, dict) else {}
        code = f"http_{status}"
        if status in (401, 403):
            raise AuthError(status, code)
        if status == 429 or status >= 500:
            raise TransientError(status, code)
        raise PermanentError(status, code)
