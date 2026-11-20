"""HTTPS client for agent-to-backend calls (TAD 11, security rule).

- Only https:// URLs. TLS certificate verification stays on (verify=True is explicit).
- The device token is sent only in the Authorization header and never logged.
- Errors are split into three kinds so callers can choose the right behavior:
  AuthError (stop sync, re-register), TransientError (back off and retry),
  PermanentError (drop the request and report it).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping
from urllib.parse import urljoin, urlparse

import requests


class HttpError(Exception):
    def __init__(self, status: int | None, code: str, detail: str = "") -> None:
        super().__init__(f"HTTP error: status={status} code={code}")
        self.status = status
        self.code = code
        # A short, safe reason for the log (for example why a certificate was refused). Never a URL or a token.
        self.detail = detail

    def for_log(self) -> str:
        return f"{self.code}: {self.detail}" if self.detail else self.code


# "certificate verify failed: <reason> (_ssl.c:1082)" -> "<reason>". Only this fixed part of an SSL error is kept.
_TLS_REASON = re.compile(r"certificate verify failed: ([A-Za-z0-9 ,'_-]{1,80})")


def _safe_detail(exc: Exception) -> str:
    """Why a TLS check failed, without the URL or host from the exception text."""
    match = _TLS_REASON.search(str(exc))
    return f"certificate verify failed: {match.group(1).strip()}" if match else ""


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

    def get_json(self, path: str, auth: bool = True, bearer: str | None = None) -> dict[str, Any]:
        return self._request("GET", path, None, auth, bearer)

    def put_absolute(self, url: str, data: bytes, headers: Mapping[str, str]) -> None:
        """Direct upload to a pre-signed S3 URL. The URL is never logged or stored."""
        if urlparse(url).scheme != "https":
            raise PermanentError(None, "upload_url_not_https")
        try:
            resp = self._session.request("PUT", url, data=data, headers=dict(headers), timeout=self._timeout, verify=self._verify, allow_redirects=False)
        except requests.RequestException as exc:
            raise TransientError(None, type(exc).__name__, _safe_detail(exc)) from None
        if resp.status_code >= 500 or resp.status_code == 429:
            raise TransientError(resp.status_code, "upload_failed")
        if resp.status_code >= 400:
            raise PermanentError(resp.status_code, "upload_rejected")

    def _request(
        self, method: str, path: str, body: Mapping[str, Any] | None, auth: bool, bearer: str | None = None
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if auth:
            token = self._token_provider()
            if not token:
                raise AuthError(None, "not_registered")
            headers["Authorization"] = f"Bearer {token}"
        elif bearer:
            # A secret the server gave this PC for one request, such as a pairing poll token.
            headers["Authorization"] = f"Bearer {bearer}"
        url = urljoin(self._base, path.lstrip("/"))
        try:
            resp = self._session.request(method, url, json=body, headers=headers, timeout=self._timeout, verify=self._verify, allow_redirects=False)
        except requests.RequestException as exc:
            # Log only the exception class, plus the fixed reason of a certificate failure. Messages can contain URLs.
            raise TransientError(None, type(exc).__name__, _safe_detail(exc)) from None
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
