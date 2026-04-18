# ID.7 Data — Project Overview

## What this project is

A hobby / learning project to pull data from a **Volkswagen ID.7 Tourer** out of the Volkswagen Cloud (WeConnect), store it locally, and display it in visually appealing dashboards. The goal is to understand what interesting insights can be derived from the data my car publishes — charging patterns, consumption, driving habits, parking behaviour — and learn Python, SQL, and data visualization along the way.

## Goals

1. **Continuously collect** vehicle telemetry from the VW Cloud into a local database.
2. **Derive insights** from the raw readings — charging sessions, trips, consumption.
3. **Visualize** both live state and historical trends in a dashboard that looks good, not just functional.
4. **Stay extensible** — keep the door open to add Home Assistant, mobile notifications, or other integrations later without rebuilding from scratch.

## Tech stack & key decisions

| Layer | Choice | Why |
|---|---|---|
| Language | **Python 3.12** | Best ecosystem for VW data libraries. Good for beginners. |
| VW Cloud access | **[CarConnectivity](https://github.com/tillsteinbach/CarConnectivity)** + `carconnectivity-connector-volkswagen` | Community library, actively maintained successor to WeConnect-python, uses the same credentials as the MyVolkswagen app. Multi-brand ready. |
| Auth path | MyVolkswagen email + password (OAuth flow handled by the library) | No commercial account / no OAuth consent flow needed. Unofficial but works today. |
| Storage | **SQLite** (single `data/id7.db` file) | Zero setup, built into Python, portable, fine for < 1 GB of time-series data. |
| Scheduling | **Linux cron** (5-minute interval) on Hetzner Cloud VPS | Runs 24/7 regardless of whether the Mac is on. Dedupes by content hash so rows are only added when the car actually reports something new. |
| Hosting | **Hetzner Cloud CX11** (~€3.29/month) | Cheap VPS in Germany (GDPR-compliant). Grafana accessible from anywhere via public IP. |
| Dashboarding | **Grafana OSS** (Docker) + [`frser-sqlite-datasource`](https://github.com/fr-ser/grafana-sqlite-datasource) plugin | Industry-standard time-series dashboards, great looking, minimal chart-writing code. Runs in a single container. |

### Paths considered and *not* taken

- **VW OKAPI** (official) — only exposes product catalogue / config data, no live telemetry.
- **Smartcar / High Mobility** — commercial aggregators, paid, require business accounts.
- **OBD-II dongle** — bypasses the cloud, different project scope.
- **Home Assistant + MQTT plugin** — more features out of the box (mobile app, notifications) but less learning per hour and more infrastructure. Deferred as a possible later addition; SQLite data is portable enough to bolt HA on later.

## Data architecture

```
VW Cloud (WeConnect API)
         │   OAuth, pull-only, no webhooks
         ▼
   collect.py  ─── every 5 min (cron on Hetzner VPS)
         │   dedup by carCapturedTimestamp
         ▼
   data/id7.db  ─────────── readings (one row per cloud update)
         │                       │
         │  derive_sessions.py   │
         ▼                       ▼
   charging_sessions        trips
         │                       │
         └──────────┬────────────┘
                    ▼
               Grafana OSS  (http://<server-ip>:3000)
                    │
                    ▼
             Dashboards (live status, SoC over time,
                          sessions, trips, parking map)
```

### SQLite tables

- **`readings`** — wide table with one row per unique `captured_at`. Contains all known fields (SoC, range, odometer, charge state, plug state, climate, position, temps, aggregate door/window/light states) **plus** a `raw_json` column holding the full `vehicle.as_dict()` payload for future-proofing against VW adding fields.
- **`charging_sessions`** — derived. Rebuilt from `readings` by detecting contiguous plug-connected + charging periods. Computes kWh added, peak/avg power, duration.
- **`trips`** — derived. Rebuilt from `readings` by detecting odometer increases between parked states. Computes distance, consumption (kWh/100 km).
- **`metadata`** — key/value for script state (`schema_version`, `battery_kwh_usable`, `last_poll_at`).

## Vehicle reference data

- **Model:** Volkswagen ID.7 Tourer
- **VIN:** `WVWZZZED4SE018925`
- **Usable battery capacity:** 77 kWh (82 kWh gross) — parameterised in `metadata.battery_kwh_usable` so it can be adjusted for the Tourer S variant (86 kWh usable).

## Current state

- ✅ **Phase 1 (Exploration spike)** complete — `explore.py` logs in, fetches the full domain tree, writes a timestamped JSON dump. Confirmed the ID.7 exposes: SoC, range, odometer, charging state, plug status, target SoC, climate state, target temp, window heating, doors / windows / lights aggregate state, parking GPS + address, outside temp, battery temp, ~40 capability flags.
- ✅ **Phase 2 (Collection + storage + Grafana)** complete — continuous collection, SQLite storage, derived sessions and trips, 10-panel Grafana dashboard. Running locally via macOS launchd.
- 🔨 **Phase 3 (Server deployment)** — next. Moving the full stack to a Hetzner Cloud VPS so collection runs 24/7 and Grafana is accessible from anywhere. See [`roadmap.md`](./roadmap.md).

## Where things live

- **This file (project goals & architecture):** `docs/project.md`
- **Roadmap / next steps:** `docs/roadmap.md`
- **Active & past feature plans:** `docs/plans/`
- **Context for Claude:** `CLAUDE.md` (project root)

## Gotchas learned so far

- VW's WeConnect API is **pull-only** — no webhooks or push channel for third parties. Frequent polling + timestamp dedup is the closest we can get to "live".
- First-time login may trigger **2FA** via browser on the same machine. Subsequent runs reuse the cached session (`.tokenstore`).
- The car reports individual door/window states **as `null`** — only aggregate "closed / open" is available.
- Trip history is not exposed by the library today — we have to derive trips from odometer + state changes.
