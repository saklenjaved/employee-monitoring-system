"""Fakes for every Windows-only interface. Used by all tests so CI runs on Linux."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

from winagent.platform_base import ForegroundInfo


class FakeProtector:
    """Stands in for DPAPI. Reversible, so tests can check round-trips. Not encryption."""

    def protect(self, data: bytes) -> bytes:
        return base64.b64encode(b"fake-dpapi:" + data)

    def unprotect(self, blob: bytes) -> bytes:
        raw = base64.b64decode(blob)
        if not raw.startswith(b"fake-dpapi:"):
            raise ValueError("not a fake DPAPI blob")
        return raw[len(b"fake-dpapi:") :]


@dataclass
class FakeForeground:
    info: ForegroundInfo | None = None
    raise_error: Exception | None = None
    last_include_title: bool | None = None

    def read(self, include_title: bool) -> ForegroundInfo | None:
        self.last_include_title = include_title
        if self.raise_error:
            raise self.raise_error
        return self.info


@dataclass
class FakeIdle:
    seconds: float = 0.0
    raise_error: Exception | None = None

    def idle_seconds(self) -> float:
        if self.raise_error:
            raise self.raise_error
        return self.seconds


@dataclass
class FakeCapturer:
    png: bytes = b"\x89PNG\r\n\x1a\nfake"
    calls: int = 0

    def capture_png(self) -> bytes:
        self.calls += 1
        return self.png


@dataclass
class FakeSession:
    kind: str = "console"
    locked: bool = False

    def session_type(self) -> str:
        return self.kind

    def is_locked(self) -> bool:
        return self.locked


@dataclass
class FakeAutostart:
    command: str | None = None

    def enable(self, command: str) -> None:
        self.command = command

    def disable(self) -> None:
        self.command = None

    def is_enabled(self) -> bool:
        return self.command is not None


@dataclass
class FakeClock:
    now_value: float = 1_000_000.0
    sleeps: list[float] = field(default_factory=list)

    def now(self) -> float:
        return self.now_value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now_value += seconds
