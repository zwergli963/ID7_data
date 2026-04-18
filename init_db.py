"""
Create (or re-open) the local SQLite database from schema.sql.

Safe to run repeatedly: every CREATE is `IF NOT EXISTS` and every seed is
`INSERT OR IGNORE`, so re-running never destroys existing data.

Usage:
    python init_db.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "schema.sql"
DB_PATH = HERE / "data" / "id7.db"

# Seed values for the `metadata` key/value table.
# Edit battery_kwh_usable to 86 if/when we switch to the Tourer S.
METADATA_SEED: dict[str, str] = {
    "schema_version": "1",
    "battery_kwh_usable": "77",
    "last_poll_at": "",
}


def main() -> int:
    DB_PATH.parent.mkdir(exist_ok=True)

    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")

    # `with sqlite3.connect(...)` commits on clean exit, rolls back on error.
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(schema_sql)
        conn.executemany(
            "INSERT OR IGNORE INTO metadata(key, value) VALUES (?, ?)",
            list(METADATA_SEED.items()),
        )

    print(f"ok: {DB_PATH} ready (schema v{METADATA_SEED['schema_version']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
