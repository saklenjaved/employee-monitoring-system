"""Connect this PC without a code (docs/employee-pairing-plan.md).

The agent asks the server for a pairing request and opens the employee's dashboard once. The employee
approves the PC there with their password. The agent polls with its secret token until the credential
arrives, then stores it with the same protected store as a registration.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from winagent.api_client import AgentApi
from winagent.credentials import CredentialStore, DeviceCredential
from winagent.http_client import HttpError, TransientError
from winagent.registration import _parse_expiry

log = logging.getLogger("winagent.pairing")

POLL_SECONDS = 15
RETRY_SECONDS = 30
DASHBOARD_MARKER = "dashboard_opened"  # in the agent's data folder; the installer deletes it on every install
# The server counts every pairing try against its limit, so a rejected or rate-limited try waits longer.
REJECTED_RETRY_SECONDS = 600


def dashboard_url(api_base_url: str) -> str:
    """The employee's dashboard, found from the API address (the same server)."""
    root = api_base_url.split("/api/agent/v1")[0].rstrip("/")
    return f"{root}/dashboard/me/"


def open_dashboard_once(url: str, marker: Path, open_browser: Callable[[str], Any]) -> None:
    """Open the dashboard unless it was already opened for this pairing. The marker file remembers that.

    The marker is removed when the PC connects, and by the installer, so every new install or new
    pairing opens the dashboard once. A restart, a sign-in or a new request does not open it again.
    A failed open is logged and tried again later, and never stops pairing.
    """
    if marker.exists():
        return
    try:
        opened = open_browser(url)
    except Exception as exc:  # no browser must never stop the PC from connecting
        log.warning("could not open the dashboard (%s)", type(exc).__name__)
        return
    if opened is False:
        log.warning("could not open the dashboard (no browser)")
        return
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("opened", encoding="ascii")
    except OSError:
        log.warning("could not write the dashboard marker")


def pair_this_pc(
    api: AgentApi,
    store: CredentialStore,
    details: Mapping[str, str],
    dashboard: str,
    stop_requested: Callable[[], bool],
    wait: Callable[[float], None],
    open_browser: Callable[[str], Any],
) -> bool:
    """Ask to connect until the employee approves. Returns True once the credential is stored.

    Returns False only when `stop_requested` becomes true. Waiting is done through `wait`, so a stop
    request is seen quickly. The dashboard is opened once, for the first request only.
    """
    opened = False
    while not stop_requested():
        try:
            request = api.create_pairing_request(**details)
        except HttpError as exc:
            log.warning("pairing request failed (%s)", exc.for_log())
            rejected = exc.status in (400, 429)
            wait(REJECTED_RETRY_SECONDS if rejected else RETRY_SECONDS)
            continue
        # Open the dashboard once, right after the first request exists, so the employee sees the approval prompt.
        # A later request (the first one expired) does not open another window.
        if not opened:
            open_browser(dashboard)
            opened = True
        if _await_approval(api, store, request, stop_requested, wait):
            return True
        if not stop_requested():
            wait(RETRY_SECONDS)
    return False


def _await_approval(api, store, request, stop_requested, wait) -> bool:
    """Poll one request until it is approved (True, credential stored) or it ends (False)."""
    expires = _parse_expiry(request["expires_at"])
    request_id, token = request["request_id"], request["poll_token"]
    while not stop_requested() and datetime.now(timezone.utc) < expires:
        wait(POLL_SECONDS)
        try:
            reply = api.poll_pairing(request_id, token)
        except TransientError:
            continue
        except HttpError as exc:
            log.warning("pairing poll ended (%s)", exc.for_log())
            return False
        status = reply.get("status")
        if status == "approved":
            store.save(
                DeviceCredential(
                    device_id=reply["device_id"],
                    token=reply["credential"],
                    expires_at=_parse_expiry(reply["expires_at"]),
                )
            )
            log.info("this PC is connected")
            return True
        if status != "pending":
            # expired, or the credential was already handed over and then lost. Start a new request.
            return False
    return False
