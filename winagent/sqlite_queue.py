"""Encrypted SQLite offline queue (TAD 5.1 local state store, TAD 11; demo decision).

Storage rules:
- Each event is encrypted with DPAPI (current user) before it is written. Only event_id,
  a sequence number and the creation time are stored in clear, and none of them is content.
- Bounded by count (oldest dropped first) and by age (expired events are deleted).
- event_id is the primary key, so a repeated add is ignored (duplicate prevention).
- Rows leave the table only when the backend acknowledges them (accepted, duplicate or rejected).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

from winagent.events import Event, from_wire, to_wire
from winagent.platform_base import SecretProtector

log = logging.getLogger("winagent.queue.sqlite")

_ACK_CHUNK = 500  # keeps well under SQLite's parameter limit


class SqliteEventQueue:
    def __init__(
        self,
        path: Path,
        protector: SecretProtector,
        max_events: int,
        max_age_seconds: float,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if max_events < 1:
            raise ValueError("max_events must be at least 1")
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._protector = protector
        self._max = max_events
        self._max_age = max_age_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._dropped = 0
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            " event_id TEXT PRIMARY KEY,"
            " seq INTEGER NOT NULL,"
            " created_at REAL NOT NULL,"
            " payload BLOB NOT NULL)"
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS ix_events_seq ON events(seq)")
        row = self._conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()
        self._seq = int(row[0])
        self._conn.commit()

    @property
    def path(self) -> Path:
        return self._path

    def add(self, event: Event) -> bool:
        blob = self._protector.protect(json.dumps(to_wire(event), separators=(",", ":")).encode("utf-8"))
        with self._lock:
            self._expire_locked()
            if self._count_locked() >= self._max:
                self._drop_oldest_locked(self._count_locked() - self._max + 1)
            self._seq += 1
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO events (event_id, seq, created_at, payload) VALUES (?, ?, ?, ?)",
                (event.event_id, self._seq, self._clock(), blob),
            )
            self._conn.commit()
            return cur.rowcount == 1

    def peek(self, limit: int) -> list[Event]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_id, payload FROM events ORDER BY seq LIMIT ?", (limit,)
            ).fetchall()
        out: list[Event] = []
        corrupt: set[str] = set()
        for event_id, blob in rows:
            try:
                out.append(from_wire(json.loads(self._protector.unprotect(blob))))
            except (ValueError, KeyError, TypeError, OSError):
                # Unreadable row (for example a changed Windows user). Drop it rather than block the queue.
                corrupt.add(event_id)
        if corrupt:
            log.warning("dropped %d unreadable queued event(s)", len(corrupt))
            self.ack(corrupt)
        return out

    def ack(self, event_ids: set[str]) -> int:
        ids = list(event_ids)
        removed = 0
        with self._lock:
            for i in range(0, len(ids), _ACK_CHUNK):
                chunk = ids[i : i + _ACK_CHUNK]
                placeholders = ",".join("?" for _ in chunk)
                cur = self._conn.execute(f"DELETE FROM events WHERE event_id IN ({placeholders})", chunk)
                removed += cur.rowcount
            self._conn.commit()
        return removed

    def depth(self) -> int:
        with self._lock:
            return self._count_locked()

    def dropped_count(self) -> int:
        return self._dropped

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _count_locked(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    def _drop_oldest_locked(self, n: int) -> None:
        if n <= 0:
            return
        cur = self._conn.execute(
            "DELETE FROM events WHERE event_id IN (SELECT event_id FROM events ORDER BY seq LIMIT ?)", (n,)
        )
        self._dropped += cur.rowcount
        self._conn.commit()

    def _expire_locked(self) -> None:
        cutoff = self._clock() - self._max_age
        cur = self._conn.execute("DELETE FROM events WHERE created_at < ?", (cutoff,))
        self._dropped += cur.rowcount
        if cur.rowcount:
            self._conn.commit()
