"""Device registration (TAD 7). Stores the issued credential encrypted at rest.

Open decision (TAD 7 and 13): registration code vs. employee sign-in. This POC implements the
registration-code path only. It is an assumption and is listed in docs/open-decisions.md.
"""

from __future__ import annotations

from datetime import datetime, timezone

from winagent.api_client import AgentApi
from winagent.credentials import CredentialStore, DeviceCredential


class RegistrationError(RuntimeError):
    pass


def _parse_expiry(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def register_device(
    api: AgentApi,
    store: CredentialStore,
    registration_code: str,
    device_name: str,
    os_version: str,
    agent_version: str,
) -> DeviceCredential:
    code = registration_code.strip()
    if len(code) < 6:
        raise RegistrationError("registration code is missing or too short")
    response = api.register_device(code, device_name, os_version, agent_version)
    try:
        credential = DeviceCredential(
            device_id=response["device_id"],
            token=response["credential"],
            expires_at=_parse_expiry(response["expires_at"]),
        )
    except (KeyError, ValueError) as exc:
        raise RegistrationError("registration response is missing required fields") from exc
    store.save(credential)
    return credential


def is_expired(credential: DeviceCredential, now: datetime | None = None) -> bool:
    current = now or datetime.now(timezone.utc)
    return credential.expires_at <= current
