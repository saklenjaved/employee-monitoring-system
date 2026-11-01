"""Website monitor: interface and stub only (TAD 5.1, 13).

Browser tracking method is not approved for v1, so this returns nothing. The event type
`website_visit` is reserved in docs/event-schema.json but is never emitted by the POC.
Replacing the stub requires the browser-support decision to be approved first (ADR 0006).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Protocol

from winagent.events import Event

log = logging.getLogger("winagent.monitors.website")


class WebsiteSource(Protocol):
    def collect(self, now: datetime) -> list[Event]: ...


class StubWebsiteMonitor:
    supported = False

    def __init__(self) -> None:
        self._logged = False

    def collect(self, now: datetime) -> list[Event]:
        if not self._logged:
            log.info("website tracking is not enabled in this POC (browser method not approved)")
            self._logged = True
        return []
