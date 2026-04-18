"""
Rebuild `charging_sessions` and `trips` from `readings`.

Strategy: full rebuild, idempotent.
  1. DELETE FROM charging_sessions;  DELETE FROM trips;
  2. Pull every `readings` row for every VIN, ordered by captured_at.
  3. Walk the rows and group *contiguous* runs where the car was charging
     or driving. Each run becomes one output row.
  4. INSERT the derived rows inside a single transaction.

With ~300 rows per day and a single car, this runs in milliseconds. If
that ever changes, we can switch to incremental derivation.

Usage:
    python derive_sessions.py
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "data" / "id7.db"

# Columns we pull from readings — everything the derivations need.
READING_SELECT = (
    "vin, captured_at, vehicle_state, odometer_km, soc_percent, "
    "charging_state, charging_power_kw, lat, lon, position_address"
)


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 UTC string back into a datetime."""
    # datetime.fromisoformat handles '+00:00' since Python 3.11.
    return datetime.fromisoformat(ts)


def _duration_seconds(start: str, end: str) -> int:
    return int((_parse_iso(end) - _parse_iso(start)).total_seconds())


def _group_contiguous(
    rows: list[sqlite3.Row], predicate_column: str, predicate_value: str
) -> Iterator[list[sqlite3.Row]]:
    """Yield contiguous runs of `rows` where row[column] == value.

    Rows are assumed already ordered by (vin, captured_at).
    Groups do not cross a VIN boundary.
    """
    current: list[sqlite3.Row] = []
    current_vin: str | None = None

    for row in rows:
        match = row[predicate_column] == predicate_value
        if match and (current_vin is None or row["vin"] == current_vin):
            current.append(row)
            current_vin = row["vin"]
        else:
            if current:
                yield current
                current = []
                current_vin = None
            # Start a fresh group if this row itself matches (after a VIN change).
            if match:
                current.append(row)
                current_vin = row["vin"]

    if current:
        yield current


def _first_non_null(values: Iterable, ) -> object:
    for v in values:
        if v is not None:
            return v
    return None


def _mean(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _charging_session_row(group: list[sqlite3.Row], battery_kwh_usable: float) -> dict:
    first, last = group[0], group[-1]
    start_soc = first["soc_percent"]
    end_soc = last["soc_percent"]
    kwh_added = None
    if start_soc is not None and end_soc is not None:
        kwh_added = (end_soc - start_soc) / 100.0 * battery_kwh_usable

    powers = [r["charging_power_kw"] for r in group if r["charging_power_kw"] is not None]
    peak = max(powers) if powers else None
    avg = _mean(powers)

    lat = _first_non_null(r["lat"] for r in group)
    lon = _first_non_null(r["lon"] for r in group)
    addr = _first_non_null(r["position_address"] for r in group)

    return {
        "vin": first["vin"],
        "started_at": first["captured_at"],
        "ended_at": last["captured_at"],
        "start_soc": start_soc,
        "end_soc": end_soc,
        "kwh_added": kwh_added,
        "peak_power_kw": peak,
        "avg_power_kw": avg,
        "duration_seconds": _duration_seconds(first["captured_at"], last["captured_at"]),
        "location_lat": lat,
        "location_lon": lon,
        "location_address": addr,
    }


def _trip_row(group: list[sqlite3.Row]) -> dict:
    first, last = group[0], group[-1]
    start_odo = first["odometer_km"]
    end_odo = last["odometer_km"]
    distance = None
    if start_odo is not None and end_odo is not None:
        distance = max(0.0, end_odo - start_odo)

    start_soc = first["soc_percent"]
    end_soc = last["soc_percent"]
    kwh_used = None
    if start_soc is not None and end_soc is not None:
        # Clamp to >= 0 so a mid-trip regen blip doesn't produce negative kWh.
        kwh_used = max(0.0, (start_soc - end_soc) / 100.0)  # fraction of pack

    consumption = None
    # We only compute consumption in kWh/100 km if we actually have both.
    if kwh_used is not None and distance and distance > 0:
        # Need the battery capacity here too — caller passes it in via closure below.
        consumption = None  # filled in by caller that has access to battery_kwh_usable

    return {
        "vin": first["vin"],
        "started_at": first["captured_at"],
        "ended_at": last["captured_at"],
        "start_odometer_km": start_odo,
        "end_odometer_km": end_odo,
        "distance_km": distance,
        "start_soc": start_soc,
        "end_soc": end_soc,
        "_soc_used_fraction": kwh_used,  # temporary; caller converts to kWh
        "start_lat": first["lat"],
        "start_lon": first["lon"],
        "start_address": first["position_address"],
        "end_lat": last["lat"],
        "end_lon": last["lon"],
        "end_address": last["position_address"],
    }


def _finalize_trip(raw: dict, battery_kwh_usable: float) -> dict:
    """Convert the SoC-fraction into kWh and compute consumption."""
    soc_used_fraction = raw.pop("_soc_used_fraction", None)
    kwh_used = (
        soc_used_fraction * battery_kwh_usable if soc_used_fraction is not None else None
    )
    distance = raw["distance_km"]
    consumption = None
    if kwh_used is not None and distance and distance > 0:
        consumption = kwh_used / distance * 100.0
    raw["kwh_used"] = kwh_used
    raw["consumption_kwh_per_100km"] = consumption
    return raw


def _battery_kwh_usable(conn: sqlite3.Connection) -> float:
    cur = conn.execute("SELECT value FROM metadata WHERE key = 'battery_kwh_usable'")
    result = cur.fetchone()
    return float(result[0]) if result else 77.0


def main() -> int:
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}. Run init_db.py first.", file=sys.stderr)
        return 1

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        battery_kwh = _battery_kwh_usable(conn)

        rows: list[sqlite3.Row] = list(
            conn.execute(
                f"SELECT {READING_SELECT} FROM readings ORDER BY vin, captured_at"
            )
        )

        # Build all derived rows before writing — keeps the transaction short.
        sessions = [
            _charging_session_row(group, battery_kwh)
            for group in _group_contiguous(rows, "charging_state", "charging")
        ]
        trips = [
            _finalize_trip(_trip_row(group), battery_kwh)
            for group in _group_contiguous(rows, "vehicle_state", "driving")
        ]

        conn.execute("DELETE FROM charging_sessions")
        conn.execute("DELETE FROM trips")

        if sessions:
            conn.executemany(
                "INSERT INTO charging_sessions "
                "(vin, started_at, ended_at, start_soc, end_soc, kwh_added, "
                " peak_power_kw, avg_power_kw, duration_seconds, "
                " location_lat, location_lon, location_address) "
                "VALUES "
                "(:vin, :started_at, :ended_at, :start_soc, :end_soc, :kwh_added, "
                " :peak_power_kw, :avg_power_kw, :duration_seconds, "
                " :location_lat, :location_lon, :location_address)",
                sessions,
            )
        if trips:
            conn.executemany(
                "INSERT INTO trips "
                "(vin, started_at, ended_at, "
                " start_odometer_km, end_odometer_km, distance_km, "
                " start_soc, end_soc, kwh_used, consumption_kwh_per_100km, "
                " start_lat, start_lon, start_address, "
                " end_lat, end_lon, end_address) "
                "VALUES "
                "(:vin, :started_at, :ended_at, "
                " :start_odometer_km, :end_odometer_km, :distance_km, "
                " :start_soc, :end_soc, :kwh_used, :consumption_kwh_per_100km, "
                " :start_lat, :start_lon, :start_address, "
                " :end_lat, :end_lon, :end_address)",
                trips,
            )

    print(
        f"ok: rebuilt derivations from {len(rows)} readings "
        f"→ {len(sessions)} charging_sessions, {len(trips)} trips "
        f"(battery={battery_kwh:g} kWh)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
