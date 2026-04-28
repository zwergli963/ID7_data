"""
ID.7 Data — one-shot collector.

Logs into VW WeConnect via CarConnectivity, pulls the latest payload for
every vehicle in the garage, and writes one new row into `readings` per
vehicle — but only if anything meaningful changed since the previous row.

Intended to be called every 5 minutes by launchd.

Dedup strategy
--------------
The CarConnectivity library's per-field `upd` timestamps are a mix of
VW-server times (seconds precision) and library wall-clock times
(microseconds). Neither is a reliable "did the car report something new?"
signal on its own. So instead of using a timestamp, we hash the set of
*dynamic* fields (state, odometer, SoC, charging state, power, position)
and compare to the most recent stored row for that VIN. If identical,
skip the insert.

Every insert still stamps `captured_at` with the wall-clock poll time and
stores the raw payload in `raw_json`, so we keep every `upd` around for
later inspection.

Usage:
    python collect.py                 # uses ./config.json
    python collect.py path/to.json    # custom config path
"""
from __future__ import annotations

import json
import logging
import signal
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Kill the process if a single poll takes longer than this.
# Prevents the collector from hanging on a stalled VW API connection.
TIMEOUT_SECONDS = 90


def _raise_timeout(signum: int, frame: object) -> None:
    raise TimeoutError(f"Collector timed out after {TIMEOUT_SECONDS}s — VW API may be unresponsive")


from carconnectivity import carconnectivity
from carconnectivity.json_util import ExtendedWithNullEncoder

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "config.json"
TOKENSTORE = HERE / ".tokenstore"
DB_PATH = HERE / "data" / "id7.db"

# Dynamic fields used for content-based dedup. If every one of these is
# identical to the most recent stored row, we skip the insert.
DEDUP_FIELDS: tuple[str, ...] = (
    "vehicle_state",
    "odometer_km",
    "soc_percent",
    "range_km",
    "charging_state",
    "charging_power_kw",
    "connector_connection",
    "lat",
    "lon",
)

# Columns written on INSERT. Order matters — must match the VALUES list.
READING_COLUMNS: tuple[str, ...] = (
    "vin", "captured_at",
    "vehicle_state", "connection_state",
    "odometer_km", "soc_percent", "range_km", "range_estimated_full_km",
    "battery_temp_k", "battery_temp_min_k", "battery_temp_max_k",
    "charging_state", "charging_type", "charging_power_kw",
    "charging_target_soc", "charging_max_current_a",
    "connector_connection", "connector_lock", "external_power",
    "climate_state", "climate_target_c",
    "window_heating_enabled", "seat_heating_enabled",
    "outside_temp_k",
    "doors_open", "doors_lock", "windows_open", "lights_state",
    "lat", "lon", "position_type", "position_address",
    "raw_json",
)


def _get(payload: dict[str, Any], dotted_path: str) -> Any:
    """Walk a dotted path through a nested dict, returning None if anything is missing.

    Example: _get(payload, "drives.primary.level.val") → 50.0
    """
    node: Any = payload
    for key in dotted_path.split("."):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _bool_to_int(value: Any) -> int | None:
    """SQLite has no bool — store True/False as 1/0, preserve None."""
    if value is None:
        return None
    return 1 if bool(value) else 0


def _flatten(vin: str, captured_at: str, payload: dict[str, Any], raw_json: str) -> dict[str, Any]:
    """Pull the wide-column fields out of a `vehicle.as_dict()` payload."""
    return {
        "vin": vin,
        "captured_at": captured_at,

        "vehicle_state": _get(payload, "state.val"),
        "connection_state": _get(payload, "connection_state.val"),

        "odometer_km": _get(payload, "odometer.val"),
        "soc_percent": _get(payload, "drives.primary.level.val"),
        "range_km": _get(payload, "drives.primary.range.val"),
        "range_estimated_full_km": _get(payload, "drives.primary.range_estimated_full.val"),
        "battery_temp_k": _get(payload, "drives.primary.battery.temperature.val"),
        "battery_temp_min_k": _get(payload, "drives.primary.battery.temperature_min.val"),
        "battery_temp_max_k": _get(payload, "drives.primary.battery.temperature_max.val"),

        "charging_state": _get(payload, "charging.state.val"),
        "charging_type": _get(payload, "charging.type.val"),
        "charging_power_kw": _get(payload, "charging.power.val"),
        "charging_target_soc": _get(payload, "charging.settings.target_level.val"),
        "charging_max_current_a": _get(payload, "charging.settings.maximum_current.val"),
        "connector_connection": _get(payload, "charging.connector.connection_state.val"),
        "connector_lock": _get(payload, "charging.connector.lock_state.val"),
        "external_power": _get(payload, "charging.connector.external_power.val"),

        "climate_state": _get(payload, "climatization.state.val"),
        "climate_target_c": _get(payload, "climatization.settings.target_temperature.val"),
        "window_heating_enabled": _bool_to_int(_get(payload, "climatization.settings.window_heating.val")),
        "seat_heating_enabled": _bool_to_int(_get(payload, "climatization.settings.seat_heating.val")),

        "outside_temp_k": _get(payload, "outside_temperature.val"),

        "doors_open": _get(payload, "doors.open_state.val"),
        "doors_lock": _get(payload, "doors.lock_state.val"),
        "windows_open": _get(payload, "windows.open_state.val"),
        "lights_state": _get(payload, "lights.light_state.val"),

        "lat": _get(payload, "position.latitude.val"),
        "lon": _get(payload, "position.longitude.val"),
        "position_type": _get(payload, "position.position_type.val"),
        "position_address": _get(payload, "position.position_location.display_name.val"),

        "raw_json": raw_json,
    }


def _latest_row(conn: sqlite3.Connection, vin: str) -> sqlite3.Row | None:
    """Return the most recent stored reading for a VIN, or None if the table is empty."""
    cur = conn.execute(
        f"SELECT {', '.join(DEDUP_FIELDS)} FROM readings "
        f"WHERE vin = ? ORDER BY captured_at DESC LIMIT 1",
        (vin,),
    )
    return cur.fetchone()


def _is_duplicate(latest: sqlite3.Row | None, row: dict[str, Any]) -> bool:
    """True if every DEDUP_FIELDS value matches the latest stored row."""
    if latest is None:
        return False
    return all(latest[field] == row[field] for field in DEDUP_FIELDS)


def _insert_reading(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    placeholders = ", ".join("?" for _ in READING_COLUMNS)
    columns = ", ".join(READING_COLUMNS)
    conn.execute(
        f"INSERT OR IGNORE INTO readings ({columns}) VALUES ({placeholders})",
        tuple(row[c] for c in READING_COLUMNS),
    )


def _update_last_poll(conn: sqlite3.Connection, when_iso: str) -> None:
    conn.execute(
        "INSERT INTO metadata(key, value) VALUES ('last_poll_at', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (when_iso,),
    )


def main() -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 1
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}. Run init_db.py first.", file=sys.stderr)
        return 1

    with config_path.open("r", encoding="utf-8") as f:
        config_dict = json.load(f)

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    cc = carconnectivity.CarConnectivity(
        config=config_dict,
        tokenstore_file=str(TOKENSTORE),
    )
    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.alarm(TIMEOUT_SECONDS)
    inserted = skipped = 0
    try:
        cc.startup()
        cc.fetch_all()

        garage = cc.get_garage()
        if garage is None:
            print("No garage returned — is the account empty or login failing?", file=sys.stderr)
            return 2
        vehicles = garage.list_vehicles()

        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row

            for vehicle in vehicles:
                vin = getattr(vehicle.vin, "value", None) if hasattr(vehicle, "vin") else vehicle.id
                if vin is None:
                    continue

                # Round-trip through JSON so enums/dates become plain strings.
                # `as_dict()` itself preserves enum objects, which SQLite can't bind.
                raw_json = json.dumps(vehicle.as_dict(), cls=ExtendedWithNullEncoder, skipkeys=True)
                payload = json.loads(raw_json)
                row = _flatten(vin, now_iso, payload, raw_json)

                latest = _latest_row(conn, vin)
                if _is_duplicate(latest, row):
                    skipped += 1
                    summary = (
                        f"skipped vin={vin} soc={row['soc_percent']} "
                        f"state={row['vehicle_state']} (no change)"
                    )
                else:
                    _insert_reading(conn, row)
                    inserted += 1
                    summary = (
                        f"inserted vin={vin} soc={row['soc_percent']} "
                        f"state={row['vehicle_state']} captured_at={now_iso}"
                    )
                print(summary)

            _update_last_poll(conn, now_iso)

        return 0
    finally:
        signal.alarm(0)  # cancel the alarm so shutdown() can complete cleanly
        cc.shutdown()


if __name__ == "__main__":
    sys.exit(main())
