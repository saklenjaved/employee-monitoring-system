"""Print a summary of the local mock backend database (demo proof). Usage: python -m mock_backend.show"""

import argparse
import sqlite3
from pathlib import Path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Summarise the mock backend data")
    parser.add_argument("--data-dir", default=Path("mock_backend/data"), type=Path)
    args = parser.parse_args(argv)
    db = args.data_dir / "backend.sqlite3"
    if not db.exists():
        print(f"no database at {db}; start the mock backend first")
        return 1
    conn = sqlite3.connect(db)
    print("devices:", conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0])
    print("events by type:")
    for kind, n in conn.execute("SELECT event_type, COUNT(*) FROM events GROUP BY event_type ORDER BY event_type"):
        print(f"  {kind}: {n}")
    print("screenshots by status:")
    for status, n in conn.execute("SELECT status, COUNT(*) FROM screenshots GROUP BY status ORDER BY status"):
        print(f"  {status}: {n}")
    last = conn.execute("SELECT received_at, body FROM heartbeats ORDER BY id DESC LIMIT 1").fetchone()
    print("last heartbeat:", last[0] if last else "none")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
