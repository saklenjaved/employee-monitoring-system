"""Local configuration (TOML) with validation. TAD 5.1 local state store.

Defaults follow the POC defaults in docs/open-decisions.md. The config file holds no secrets.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

IDLE_THRESHOLD_DEFAULT = 300
SAMPLE_INTERVAL_DEFAULT = 30  # default, to confirm: 30 s, configurable
SCREENSHOT_INTERVAL_DEFAULT = 600  # default, to confirm: 10 minutes, configurable
DEFAULT_EXCLUDED_APPS = ("KeePass.exe", "KeePassXC.exe", "1Password.exe", "Bitwarden.exe")
DEFAULT_TITLE_KEYWORDS = ("password", "incognito", "inprivate", "private browsing")
MAX_BATCH_SIZE = 500
QUEUE_MAX_EVENTS_DEFAULT = 10_000
QUEUE_MAX_EVENTS_LIMIT = 100_000


class ConfigError(ValueError):
    """Raised when the config file is missing a required value or has an invalid one."""


def default_data_dir() -> Path:
    """Per-user data folder. No admin rights needed (POC default)."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "WinAgent"


@dataclass(frozen=True)
class ApiConfig:
    base_url: str
    timeout_seconds: float = 20.0
    # Optional CA bundle for the local demo certificate. Verification stays on.
    ca_bundle: str | None = None

    def __post_init__(self) -> None:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ConfigError("api.base_url must be an https:// URL (TAD 11, TLS for every call)")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 120:
            raise ConfigError("api.timeout_seconds must be between 0 and 120")


@dataclass(frozen=True)
class MonitoringConfig:
    idle_threshold_seconds: int = IDLE_THRESHOLD_DEFAULT
    sample_interval_seconds: int = SAMPLE_INTERVAL_DEFAULT
    sync_interval_seconds: int = 60
    heartbeat_interval_seconds: int = 300
    policy_refresh_seconds: int = 3600
    batch_size: int = MAX_BATCH_SIZE
    queue_max_events: int = QUEUE_MAX_EVENTS_DEFAULT
    queue_max_age_hours: int = 72
    capture_window_title: bool = True  # demo decision: titles on, with the exclusions below
    excluded_apps: tuple[str, ...] = DEFAULT_EXCLUDED_APPS
    title_keywords_excluded: tuple[str, ...] = DEFAULT_TITLE_KEYWORDS

    def __post_init__(self) -> None:
        if not 1 <= self.queue_max_age_hours <= 24 * 30:
            raise ConfigError("monitoring.queue_max_age_hours must be 1..720")
        if not 60 <= self.idle_threshold_seconds <= 3600:
            raise ConfigError("monitoring.idle_threshold_seconds must be 60..3600")
        if not 5 <= self.sample_interval_seconds <= 300:
            raise ConfigError("monitoring.sample_interval_seconds must be 5..300")
        if not 1 <= self.batch_size <= MAX_BATCH_SIZE:
            raise ConfigError(f"monitoring.batch_size must be 1..{MAX_BATCH_SIZE} (TAD 6, 8)")
        if not 1 <= self.queue_max_events <= QUEUE_MAX_EVENTS_LIMIT:
            raise ConfigError(f"monitoring.queue_max_events must be 1..{QUEUE_MAX_EVENTS_LIMIT}")
        for name in ("sync_interval_seconds", "heartbeat_interval_seconds", "policy_refresh_seconds"):
            if getattr(self, name) < 10:
                raise ConfigError(f"monitoring.{name} must be at least 10")


@dataclass(frozen=True)
class ScreenshotConfig:
    # Off by default (POC default). Policy from the backend can still enable it.
    enabled: bool = False
    interval_seconds: int = SCREENSHOT_INTERVAL_DEFAULT
    max_upload_bytes: int = 10 * 1024 * 1024

    def __post_init__(self) -> None:
        if not 15 <= self.interval_seconds <= 3600:
            raise ConfigError("screenshots.interval_seconds must be 15..3600")


@dataclass(frozen=True)
class AgentConfig:
    api: ApiConfig
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    screenshots: ScreenshotConfig = field(default_factory=ScreenshotConfig)
    data_dir: Path = field(default_factory=default_data_dir)

    @property
    def credential_path(self) -> Path:
        return self.data_dir / "credential.bin"

    @property
    def health_path(self) -> Path:
        return self.data_dir / "health.json"

    @property
    def queue_path(self) -> Path:
        return self.data_dir / "queue.sqlite3"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def temp_dir(self) -> Path:
        return self.data_dir / "tmp"


def load_config(path: Path) -> AgentConfig:
    """Read a TOML config file. Unknown sections are rejected so typos do not pass silently."""
    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config file is not valid TOML: {exc}") from exc

    allowed = {"api", "monitoring", "screenshots", "paths", "registration"}
    unknown = set(raw) - allowed
    if unknown:
        raise ConfigError(f"unknown config section(s): {sorted(unknown)}")

    if "api" not in raw or "base_url" not in raw["api"]:
        raise ConfigError("api.base_url is required")

    api = ApiConfig(**raw["api"])
    if api.ca_bundle is not None and not Path(api.ca_bundle).is_file():
        raise ConfigError(f"api.ca_bundle file not found: {api.ca_bundle}")
    monitoring_raw = dict(raw.get("monitoring", {}))
    for key in ("excluded_apps", "title_keywords_excluded"):
        if key in monitoring_raw:
            monitoring_raw[key] = tuple(monitoring_raw[key])
    monitoring = MonitoringConfig(**monitoring_raw)
    screenshots = ScreenshotConfig(**raw.get("screenshots", {}))
    paths = raw.get("paths", {})
    data_dir = Path(paths["data_dir"]) if "data_dir" in paths else default_data_dir()
    return AgentConfig(api=api, monitoring=monitoring, screenshots=screenshots, data_dir=data_dir)


def load_registration_code(path: Path) -> str | None:
    """The local test registration code from [registration] code. Optional; --code overrides it."""
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8-sig"))
    code = raw.get("registration", {}).get("code")
    return str(code) if code else None
