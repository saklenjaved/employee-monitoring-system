"""Encrypted SQLite offline queue tests (demo decision; TAD 5.1, 8, 11)."""

from datetime import datetime, timezone

from tests.fakes import FakeProtector
from winagent.events import Event
from winagent.sqlite_queue import SqliteEventQueue

T = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)
SECRET_TITLE = "PRIVATE-DOC-TITLE-XYZ"


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def ev(title: str | None = None) -> Event:
    payload = {"process_name": "notepad.exe", "duration_seconds": 10.0}
    if title is not None:
        payload["window_title"] = title
    return Event("app_focus", T, payload)


def make(tmp_path, max_events=100, max_age=3600.0, clock=None):
    return SqliteEventQueue(tmp_path / "queue.sqlite3", FakeProtector(), max_events, max_age, clock=clock or Clock())


def test_add_peek_ack_round_trip(tmp_path):
    q = make(tmp_path)
    e = ev("Notes")
    assert q.add(e) is True
    got = q.peek(10)
    assert got[0].event_id == e.event_id
    assert got[0].payload["window_title"] == "Notes"
    assert got[0].captured_at == T
    assert q.ack({e.event_id}) == 1
    assert q.depth() == 0
    q.close()


def test_survives_restart(tmp_path):
    q = make(tmp_path)
    events = [ev() for _ in range(5)]
    for e in events:
        q.add(e)
    q.close()
    reopened = make(tmp_path)
    assert reopened.depth() == 5
    assert [e.event_id for e in reopened.peek(10)] == [e.event_id for e in events]
    reopened.close()


def test_payload_is_encrypted_at_rest(tmp_path):
    q = make(tmp_path)
    q.add(ev(SECRET_TITLE))
    q.close()
    raw = (tmp_path / "queue.sqlite3").read_bytes()
    assert SECRET_TITLE.encode() not in raw
    assert b"notepad.exe" not in raw


def test_duplicate_event_id_is_ignored(tmp_path):
    q = make(tmp_path)
    e = ev()
    assert q.add(e) is True
    assert q.add(e) is False
    assert q.depth() == 1
    q.close()


def test_bounded_by_count_oldest_dropped_and_counted(tmp_path):
    q = make(tmp_path, max_events=3)
    events = [ev() for _ in range(5)]
    for e in events:
        q.add(e)
    assert q.depth() == 3
    assert q.dropped_count() == 2
    assert [e.event_id for e in q.peek(10)] == [e.event_id for e in events[2:]]
    q.close()


def test_expired_events_are_removed(tmp_path):
    clock = Clock()
    q = make(tmp_path, max_age=60.0, clock=clock)
    q.add(ev())
    clock.now += 61
    q.add(ev())  # adding runs the age check
    assert q.depth() == 1
    assert q.dropped_count() == 1
    q.close()


def test_unreadable_row_is_dropped_not_blocking(tmp_path):
    q = make(tmp_path)
    good = ev()
    q.add(good)
    q._conn.execute("INSERT INTO events VALUES ('bad-row', 99, 0, ?)", (b"not-a-valid-blob",))
    q._conn.commit()
    got = q.peek(10)
    assert [e.event_id for e in got] == [good.event_id]
    assert q.depth() == 1
    q.close()


def test_ack_handles_more_than_one_chunk(tmp_path):
    q = make(tmp_path, max_events=2000)
    events = [ev() for _ in range(1200)]
    for e in events:
        q.add(e)
    assert q.ack({e.event_id for e in events}) == 1200
    assert q.depth() == 0
    q.close()
