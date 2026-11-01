from pathlib import Path

import pytest

from winagent.config import ConfigError, load_config

VALID = """
[api]
base_url = "https://api.example.invalid/api/agent/v1"

[paths]
data_dir = "{data}"
"""


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "agent.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_defaults_match_poc_decisions(tmp_path):
    cfg = load_config(write(tmp_path, VALID.format(data=tmp_path.as_posix())))
    assert cfg.monitoring.idle_threshold_seconds == 300
    assert cfg.monitoring.sample_interval_seconds == 30  # default, to confirm
    assert cfg.monitoring.batch_size == 500
    assert cfg.monitoring.capture_window_title is True  # demo decision
    assert "KeePass.exe" in cfg.monitoring.excluded_apps
    assert cfg.screenshots.enabled is False
    assert cfg.screenshots.interval_seconds == 600  # default, to confirm


def test_http_url_is_rejected(tmp_path):
    text = VALID.replace("https://", "http://").format(data=tmp_path.as_posix())
    with pytest.raises(ConfigError, match="https"):
        load_config(write(tmp_path, text))


def test_batch_size_cannot_exceed_500(tmp_path):
    text = VALID.format(data=tmp_path.as_posix()) + "\n[monitoring]\nbatch_size = 501\n"
    with pytest.raises(ConfigError, match="batch_size"):
        load_config(write(tmp_path, text))


def test_unknown_section_is_rejected(tmp_path):
    text = VALID.format(data=tmp_path.as_posix()) + "\n[secrets]\ntoken = 'x'\n"
    with pytest.raises(ConfigError, match="unknown"):
        load_config(write(tmp_path, text))


def test_missing_base_url_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="base_url"):
        load_config(write(tmp_path, "[api]\n"))


def test_screenshot_interval_bounds(tmp_path):
    text = VALID.format(data=tmp_path.as_posix()) + "\n[screenshots]\nenabled = true\ninterval_seconds = 10\n"
    with pytest.raises(ConfigError, match="interval"):
        load_config(write(tmp_path, text))
