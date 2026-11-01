"""The six agent API capabilities (TAD 10). Paths and shapes follow docs/api-contract.yaml.

Ownership rule: no request ever carries organization_id or employee_id. The server
resolves both from the device credential.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from winagent.http_client import HttpClient
from winagent.events import Event, to_wire


class AgentApi:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def register_device(
        self,
        registration_code: str,
        device_name: str,
        os_version: str,
        agent_version: str,
    ) -> dict[str, Any]:
        body = {
            "registration_code": registration_code,
            "device_name": device_name,
            "os_version": os_version,
            "architecture": "x64",
            "agent_version": agent_version,
        }
        return self._http.post_json("devices/register/", body, auth=False)

    def get_config(self) -> dict[str, Any]:
        return self._http.get_json("config/")

    def ingest_events(self, events: list[Event]) -> dict[str, Any]:
        return self._http.post_json("events/batch/", {"events": [to_wire(e) for e in events]})

    def authorize_screenshot(self, metadata: Mapping[str, Any]) -> dict[str, Any]:
        return self._http.post_json("screenshots/authorize/", dict(metadata))

    def complete_screenshot(self, screenshot_id: str, status: str, error_code: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"status": status}
        if error_code:
            body["error_code"] = error_code
        return self._http.post_json(f"screenshots/{screenshot_id}/complete/", body)

    def heartbeat(
        self,
        agent_version: str,
        sent_at: datetime,
        session_type: str,
        queue_depth: int,
        last_successful_sync_at: datetime | None,
        last_error_code: str | None,
        monitoring_state: str,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "agent_version": agent_version,
            "sent_at": sent_at.isoformat().replace("+00:00", "Z"),
            "session_type": session_type,
            "queue_depth": queue_depth,
            "monitoring_state": monitoring_state,
        }
        if last_successful_sync_at:
            body["last_successful_sync_at"] = last_successful_sync_at.isoformat().replace("+00:00", "Z")
        if last_error_code:
            body["last_error_code"] = last_error_code
        return self._http.post_json("heartbeat/", body)
