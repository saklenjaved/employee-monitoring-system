from pathlib import Path

from winagent.health import HealthSnapshot, read_health, status_line, write_health


def test_health_round_trip_and_atomic_write(tmp_path: Path):
    path = tmp_path / "health.json"
    snap = HealthSnapshot(agent_version="0.1.0", monitoring_state="active", queue_depth=3)
    write_health(path, snap)
    assert not (tmp_path / "health.tmp").exists()
    assert read_health(path) == snap


def test_status_line_labels_each_state():
    for state, label in {
        "active": "Monitoring active",
        "paused_hours": "Outside monitoring hours",
        "unsupported_session": "Session type not supported",
        "not_registered": "Not registered",
    }.items():
        assert status_line(HealthSnapshot("0.1.0", monitoring_state=state)).startswith(label)


def test_read_missing_health_returns_none(tmp_path: Path):
    assert read_health(tmp_path / "nope.json") is None
