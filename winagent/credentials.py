"""Device credential storage. Encrypted at rest with DPAPI; never plaintext (TAD 11, security rule).

There is no plaintext fallback. If no protector is available, storing fails closed.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from winagent.platform_base import SecretProtector


class CredentialError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeviceCredential:
    device_id: str
    token: str
    expires_at: datetime

    def __repr__(self) -> str:  # never show the token in logs or tracebacks
        return f"DeviceCredential(device_id={self.device_id!r}, token=<redacted>, expires_at={self.expires_at.isoformat()})"


class CredentialStore:
    def __init__(self, path: Path, protector: SecretProtector) -> None:
        self._path = Path(path)
        self._protector = protector

    @property
    def path(self) -> Path:
        return self._path

    def save(self, credential: DeviceCredential) -> None:
        payload = json.dumps(
            {
                "device_id": credential.device_id,
                "token": credential.token,
                "expires_at": credential.expires_at.isoformat(),
            }
        ).encode("utf-8")
        blob = self._protector.protect(payload)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, self._path)

    def load(self) -> DeviceCredential | None:
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._protector.unprotect(self._path.read_bytes()))
            return DeviceCredential(
                device_id=data["device_id"],
                token=data["token"],
                expires_at=datetime.fromisoformat(data["expires_at"]),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise CredentialError("stored credential is unreadable; re-registration is required") from exc

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()
