"""The upload interval comes from the policy, within bounds, and falls back to the local default."""
import pytest

from winagent.agent_policy import PolicyError, parse_policy
from winagent.config import MonitoringConfig, ScreenshotConfig

MONITORING = {"enabled": True, "days": [0, 1, 2, 3, 4], "start": "09:00", "end": "18:00"}


def policy_with(activity):
    return {"policy_version": "7", "monitoring": MONITORING, "activity": activity}


def test_the_server_upload_interval_is_used():
    parsed = parse_policy(policy_with({"sync_interval_seconds": 120}), MonitoringConfig(), ScreenshotConfig())
    assert parsed.sync_interval_seconds == 120


def test_a_missing_interval_keeps_the_local_default():
    local = MonitoringConfig(sync_interval_seconds=90)
    parsed = parse_policy(policy_with({}), local, ScreenshotConfig())
    assert parsed.sync_interval_seconds == 90


@pytest.mark.parametrize("value", [29, 901])
def test_an_interval_outside_the_bounds_is_rejected(value):
    with pytest.raises(PolicyError):
        parse_policy(policy_with({"sync_interval_seconds": value}), MonitoringConfig(), ScreenshotConfig())
