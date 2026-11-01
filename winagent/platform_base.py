"""Interfaces for every Windows-only call (TAD 5.2). Implementations live in platform_win.py.

Tests use the fakes in tests/fakes.py, so CI runs on Linux.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ForegroundInfo:
    process_name: str  # executable file name only, for example "chrome.exe"
    window_title: str | None  # None unless the caller asked for titles


class ForegroundWindowReader(Protocol):
    def read(self, include_title: bool) -> ForegroundInfo | None: ...


class IdleTimeReader(Protocol):
    """Seconds since last keyboard or mouse input. Returns a timestamp difference only."""

    def idle_seconds(self) -> float: ...


class ScreenCapturer(Protocol):
    def capture_png(self) -> bytes: ...


class SessionInspector(Protocol):
    def session_type(self) -> str: ...  # "console", "rdp", "citrix", "vdi" or "unknown"

    def is_locked(self) -> bool: ...


class SecretProtector(Protocol):
    """Encrypts bytes for the current Windows user (DPAPI in production)."""

    def protect(self, data: bytes) -> bytes: ...

    def unprotect(self, blob: bytes) -> bytes: ...


class AutostartRegistry(Protocol):
    def enable(self, command: str) -> None: ...

    def disable(self) -> None: ...

    def is_enabled(self) -> bool: ...
