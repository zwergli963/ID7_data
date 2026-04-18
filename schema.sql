-- ID.7 Data — SQLite schema (v1)
--
-- Four tables:
--   readings            : one row per unique (vin, captured_at), wide column set
--                         for the interesting fields plus the full raw payload.
--   charging_sessions   : derived from readings by derive_sessions.py.
--   trips               : derived from readings by derive_sessions.py.
--   metadata            : key/value bag for script state + tunables.
--
-- Timestamps are ISO-8601 UTC strings (e.g. "2026-04-16T15:22:06+00:00").
-- Temperatures are stored raw in Kelvin; converted to °C in Grafana.

CREATE TABLE IF NOT EXISTS readings (
  id                         INTEGER PRIMARY KEY AUTOINCREMENT,
  vin                        TEXT    NOT NULL,
  captured_at                TEXT    NOT NULL,

  -- top-level state
  vehicle_state              TEXT,   -- parked / driving / ...
  connection_state           TEXT,   -- reachable / ...

  -- drive & battery
  odometer_km                REAL,
  soc_percent                REAL,
  range_km                   REAL,
  range_estimated_full_km    REAL,
  battery_temp_k             REAL,
  battery_temp_min_k         REAL,
  battery_temp_max_k         REAL,

  -- charging
  charging_state             TEXT,   -- off / charging / ...
  charging_type              TEXT,   -- ac / dc / invalid
  charging_power_kw          REAL,
  charging_target_soc        REAL,
  charging_max_current_a     REAL,
  connector_connection       TEXT,   -- connected / disconnected
  connector_lock             TEXT,   -- locked / unlocked
  external_power             TEXT,   -- available / unavailable / ...

  -- climate
  climate_state              TEXT,
  climate_target_c           REAL,
  window_heating_enabled     INTEGER,  -- 0/1
  seat_heating_enabled       INTEGER,  -- 0/1

  -- environment
  outside_temp_k             REAL,

  -- aggregate body states
  doors_open                 TEXT,
  doors_lock                 TEXT,
  windows_open               TEXT,
  lights_state               TEXT,

  -- position
  lat                        REAL,
  lon                        REAL,
  position_type              TEXT,   -- parking / driving
  position_address           TEXT,

  -- full payload as returned by vehicle.as_dict(), future-proofing
  raw_json                   TEXT    NOT NULL,

  UNIQUE(vin, captured_at)
);

CREATE INDEX IF NOT EXISTS idx_readings_captured_at     ON readings(captured_at);
CREATE INDEX IF NOT EXISTS idx_readings_vin_captured_at ON readings(vin, captured_at);

CREATE TABLE IF NOT EXISTS charging_sessions (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  vin               TEXT NOT NULL,
  started_at        TEXT NOT NULL,
  ended_at          TEXT,             -- NULL if still ongoing
  start_soc         REAL,
  end_soc           REAL,
  kwh_added         REAL,             -- (end_soc - start_soc) / 100 * battery_kwh_usable
  peak_power_kw     REAL,
  avg_power_kw      REAL,
  duration_seconds  INTEGER,
  location_lat      REAL,
  location_lon      REAL,
  location_address  TEXT,
  UNIQUE(vin, started_at)
);

CREATE TABLE IF NOT EXISTS trips (
  id                         INTEGER PRIMARY KEY AUTOINCREMENT,
  vin                        TEXT NOT NULL,
  started_at                 TEXT NOT NULL,
  ended_at                   TEXT NOT NULL,
  start_odometer_km          REAL,
  end_odometer_km            REAL,
  distance_km                REAL,
  start_soc                  REAL,
  end_soc                    REAL,
  kwh_used                   REAL,   -- clamped to >= 0
  consumption_kwh_per_100km  REAL,
  start_lat                  REAL,
  start_lon                  REAL,
  start_address              TEXT,
  end_lat                    REAL,
  end_lon                    REAL,
  end_address                TEXT,
  UNIQUE(vin, started_at)
);

CREATE TABLE IF NOT EXISTS metadata (
  key    TEXT PRIMARY KEY,
  value  TEXT NOT NULL
);
