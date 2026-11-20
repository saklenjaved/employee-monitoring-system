"""Turns the backend policy response into local settings (TAD 6, 10: configuration / policy).

Server values are checked against the same bounds as the local config. Anything out of
range is rejected, and the local value is kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from winagent.config import MonitoringConfig, ScreenshotConfig
from winagent.screenshots.policy import Rect, ScreenshotPolicy
from winagent.schedule import MonitoringWindow


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class AgentPolicy:
    version: str
    window: MonitoringWindow
    sample_interval_seconds: int
    idle_threshold_seconds: int
    screenshots: ScreenshotPolicy
    excluded_apps: frozenset[str] = field(default_factory=frozenset)
    sync_interval_seconds: int = 60


def parse_policy(raw: dict[str, Any], local_monitoring: MonitoringConfig, local_shots: ScreenshotConfig) -> AgentPolicy:
    try:
        monitoring = raw["monitoring"]
        window = MonitoringWindow.from_policy(monitoring)
    except (KeyError, ValueError, TypeError) as exc:
        raise PolicyError("policy monitoring block is invalid") from exc

    activity = raw.get("activity", {})
    sample = _bounded(activity.get("sample_interval_seconds"), 5, 300, local_monitoring.sample_interval_seconds)
    idle = _bounded(activity.get("idle_threshold_seconds"), 60, 3600, local_monitoring.idle_threshold_seconds)
    sync = _bounded(activity.get("sync_interval_seconds"), 30, 900, local_monitoring.sync_interval_seconds)

    shots_raw = raw.get("screenshots", {})
    exclusions = raw.get("exclusions", {})
    blur = tuple(
        Rect(int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])) for r in exclusions.get("blur_regions", [])
    )
    apps = frozenset(str(a) for a in exclusions.get("apps", []))
    shot_policy = ScreenshotPolicy(
        enabled=bool(shots_raw.get("enabled", False)),
        interval_seconds=_bounded(shots_raw.get("interval_seconds"), 15, 3600, local_shots.interval_seconds),
        window=window,
        excluded_apps=apps,
        blur_regions=blur,
    )
    return AgentPolicy(
        version=str(raw.get("policy_version", "unknown")),
        window=window,
        sample_interval_seconds=sample,
        idle_threshold_seconds=idle,
        screenshots=shot_policy,
        excluded_apps=apps,
        sync_interval_seconds=sync,
    )


def _bounded(value: Any, low: int, high: int, fallback: int) -> int:
    if value is None:
        return fallback
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise PolicyError("policy value is not an integer") from exc
    if not low <= number <= high:
        raise PolicyError(f"policy value {number} outside {low}..{high}")
    return number
